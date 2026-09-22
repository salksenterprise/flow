from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from isrp.orchestration import ConflictError, WorkflowEngine
from isrp.orchestration import SQLiteWorkflowRepository

from support import aggregate_args


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = json.loads((ROOT / "isrp/templates/information-security-review.json").read_text())


class WorkflowEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = SQLiteWorkflowRepository(Path(self.temp.name) / "workflow.db")
        self.repository.initialize()
        self.engine = WorkflowEngine(self.repository)
        self.version_id = self.engine.import_template(TEMPLATE)["workflow_version_id"]

    def tearDown(self):
        self.temp.cleanup()

    def start(self, command_id: str | None = None, identity: bool = True, network: bool = True):
        return self.engine.start_request({
            "command_id": command_id or str(uuid.uuid4()),
            "workflow_version_id": self.version_id,
            "title": "External assessment workflow",
            "actor": "isr.service",
            "variables": {"identity_review_required": identity, "network_review_required": network},
        })

    def action(self, workflow: dict, step_key: str, action: str, command_id: str | None = None):
        step = next(item for item in workflow["steps"] if item["step_key"] == step_key)
        return self.engine.apply_action(step["id"], {
            "command_id": command_id or str(uuid.uuid4()), "action": action,
            "actor": "tester", "expected_revision": workflow["revision"], "payload": {},
        })

    def complete(self, workflow: dict, step_key: str):
        workflow = self.action(workflow, step_key, "start")
        return self.action(workflow, step_key, "complete")

    def test_parallel_join_and_completion(self):
        workflow = self.start()
        for key in ("intake", "categorize", "design"):
            workflow = self.complete(workflow, key)
        states = {step["step_key"]: step["state"] for step in workflow["steps"]}
        self.assertEqual(states["identity_sme"], "READY")
        self.assertEqual(states["network_sme"], "READY")
        self.assertEqual(states["architecture_sme"], "READY")
        self.assertEqual(states["join_sme"], "NOT_READY")
        for key in ("identity_sme", "network_sme", "architecture_sme", "consolidate", "build", "validate"):
            workflow = self.complete(workflow, key)
        self.assertEqual(workflow["execution_status"], "COMPLETED")

    def test_conditional_branch(self):
        workflow = self.start(identity=False)
        for key in ("intake", "categorize", "design"):
            workflow = self.complete(workflow, key)
        states = {step["step_key"]: step["state"] for step in workflow["steps"]}
        self.assertEqual(states["identity_sme"], "NOT_READY")
        self.assertEqual(states["network_sme"], "READY")

    def test_clarification_cycle(self):
        workflow = self.start()
        workflow = self.action(workflow, "intake", "start")
        workflow = self.action(workflow, "intake", "request_clarification")
        workflow = self.action(workflow, "intake", "respond")
        workflow = self.action(workflow, "intake", "resume")
        workflow = self.action(workflow, "intake", "complete")
        self.assertEqual(next(x for x in workflow["steps"] if x["step_key"] == "categorize")["state"], "READY")

    def test_start_command_is_idempotent(self):
        command_id = str(uuid.uuid4())
        first = self.start(command_id=command_id)
        second = self.start(command_id=command_id)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.engine.list_requests()), 1)

    def test_revision_conflict(self):
        workflow = self.start()
        step = next(item for item in workflow["steps"] if item["step_key"] == "intake")
        with self.assertRaises(ConflictError):
            self.engine.apply_action(step["id"], {
                "command_id": str(uuid.uuid4()), "action": "start", "actor": "tester",
                "expected_revision": 0, "payload": {},
            })

    def test_events_are_written_to_outbox(self):
        workflow = self.start()
        with self.repository.transaction():
            events = self.repository.db.execute(
                "SELECT COUNT(*) FROM event_log WHERE aggregate_type=? AND aggregate_id=?",
                aggregate_args(workflow),
            ).fetchone()[0]
            outbox = self.repository.db.execute("SELECT COUNT(*) FROM outbox_event").fetchone()[0]
        self.assertEqual(events, outbox)
        self.assertGreater(events, 0)


if __name__ == "__main__":
    unittest.main()

