"""Moving a running instance between definition versions (DEF-9)."""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from isrp.orchestration import ConflictError, ValidationError, WorkflowEngine
from isrp.orchestration import SQLiteWorkflowRepository

from support import owner


def command(**values):
    return {"command_id": str(uuid.uuid4()), "actor": "tester", **values}


MIGRATOR = {"actor_id": "release.manager", "permissions": ["workflow.migrate"]}


def template(key, version, steps, transitions, lifecycle=None):
    body = {"key": key, "name": key, "version": version, "publish": True,
            "steps": steps, "transitions": transitions}
    if lifecycle:
        body["lifecycle_fsm"] = lifecycle
    return body


def human(key):
    return {"key": key, "name": key, "type": "HUMAN_TASK", "configuration": {}}


class VersionMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = SQLiteWorkflowRepository(Path(self.temp.name) / "workflow.db")
        self.repository.initialize()
        self.engine = WorkflowEngine(self.repository)

        self.v1 = self.engine.import_template(template(
            "migrating", 1,
            [human("intake"), human("review"), {"key": "end", "name": "End", "type": "END"}],
            [{"from_step": "intake", "to_step": "review"},
             {"from_step": "review", "to_step": "end"}]))["workflow_version_id"]
        self.v2 = self.engine.import_template(template(
            "migrating", 2,
            [human("intake"), human("review"), human("second_approval"),
             {"key": "end", "name": "End", "type": "END"}],
            [{"from_step": "intake", "to_step": "review"},
             {"from_step": "review", "to_step": "second_approval"},
             {"from_step": "second_approval", "to_step": "end"}]))["workflow_version_id"]

    def tearDown(self):
        self.temp.cleanup()

    def start(self, version_id):
        return self.engine.start_request(command(
            workflow_version_id=version_id, title="T",
            variables={}))

    def migrate(self, workflow, target, **overrides):
        body = dict(target_version_id=target, reason="Policy change approved",
                    expected_revision=workflow["revision"], actor=MIGRATOR)
        body.update(overrides)
        return self.engine.migrate_version(owner(workflow), command(**body))

    def keys(self, workflow):
        return {item["step_key"]: item["execution_status"] for item in workflow["steps"]}

    def test_def9_migration_keeps_shared_nodes_and_adds_new_ones(self):
        workflow = self.start(self.v1)
        self.assertEqual(set(self.keys(workflow)), {"intake", "review", "end"})

        migrated = self.migrate(workflow, self.v2)

        self.assertEqual(migrated["version_number"], 2)
        self.assertEqual(self.keys(migrated)["intake"], "READY")
        self.assertEqual(self.keys(migrated)["second_approval"], "NOT_READY")

    def test_def9_migrated_instance_runs_the_new_graph_to_completion(self):
        workflow = self.migrate(self.start(self.v1), self.v2)
        for key in ("intake", "review", "second_approval"):
            step = next(item for item in workflow["steps"] if item["step_key"] == key)
            workflow = self.engine.apply_action(step["id"], command(
                action="start", expected_revision=workflow["revision"], payload={}))
            workflow = self.engine.apply_action(step["id"], command(
                action="complete", expected_revision=workflow["revision"], payload={}))
        self.assertEqual(workflow["execution_status"], "COMPLETED")

    def test_def9_a_removed_node_is_retired(self):
        trimmed = self.engine.import_template(template(
            "migrating", 3,
            [human("intake"), {"key": "end", "name": "End", "type": "END"}],
            [{"from_step": "intake", "to_step": "end"}]))["workflow_version_id"]
        migrated = self.migrate(self.start(self.v1), trimmed)
        self.assertEqual(self.keys(migrated)["review"], "CANCELLED")

    def test_def9_requires_permission_and_a_reason(self):
        workflow = self.start(self.v1)
        with self.assertRaises(ConflictError):
            self.migrate(workflow, self.v2, actor={"actor_id": "nobody", "permissions": []})
        with self.assertRaises(ValidationError):
            self.migrate(workflow, self.v2, reason="")

    def test_def9_refuses_while_work_is_in_flight(self):
        workflow = self.start(self.v1)
        step = next(item for item in workflow["steps"] if item["step_key"] == "intake")
        workflow = self.engine.apply_action(step["id"], command(
            action="start", expected_revision=workflow["revision"], payload={}))
        with self.assertRaises(ConflictError) as raised:
            self.migrate(workflow, self.v2)
        self.assertIn("intake", str(raised.exception))

    def test_def9_refuses_a_version_of_a_different_definition(self):
        other = self.engine.import_template(template(
            "unrelated", 1, [human("work"), {"key": "end", "name": "End", "type": "END"}],
            [{"from_step": "work", "to_step": "end"}]))["workflow_version_id"]
        with self.assertRaises(ValidationError):
            self.migrate(self.start(self.v1), other)

    def test_def9_refuses_a_target_whose_lifecycle_lacks_the_current_state(self):
        lifecycle = {
            "key": "migrating.strict", "name": "Strict", "version": 1,
            "initial_state": "TRIAGE",
            "states": ["TRIAGE", {"key": "DONE", "terminal": True}],
            "transitions": [{"action": "complete", "from": "TRIAGE", "to": "DONE"}],
        }
        strict = self.engine.import_template(template(
            "migrating", 4,
            [human("intake"), {"key": "end", "name": "End", "type": "END"}],
            [{"from_step": "intake", "to_step": "end"}], lifecycle))["workflow_version_id"]
        with self.assertRaises(ConflictError) as raised:
            self.migrate(self.start(self.v1), strict)
        self.assertIn("lifecycle status", str(raised.exception))

    def test_def9_refuses_a_target_step_fsm_missing_the_current_state(self):
        incompatible_fsm = {
            "key": "migrating.incompatible-step", "name": "Incompatible", "version": 1,
            "initial_state": "PENDING",
            "states": ["PENDING", {"key": "DONE", "terminal": True}],
            "transitions": [
                {"action": "activate", "from": "PENDING", "to": "DONE"}],
        }
        incompatible = human("intake")
        incompatible["fsm"] = incompatible_fsm
        target = self.engine.import_template(template(
            "migrating", 5,
            [incompatible, human("review"), {"key": "end", "name": "End", "type": "END"}],
            [{"from_step": "intake", "to_step": "review"},
             {"from_step": "review", "to_step": "end"}]))["workflow_version_id"]

        with self.assertRaises(ConflictError) as raised:
            self.migrate(self.start(self.v1), target)
        self.assertIn("current state", str(raised.exception))

    def test_def9_refuses_a_completed_workflow_and_a_no_op(self):
        workflow = self.start(self.v1)
        with self.assertRaises(ConflictError):
            self.migrate(workflow, self.v1)

        for key in ("intake", "review"):
            step = next(item for item in workflow["steps"] if item["step_key"] == key)
            workflow = self.engine.apply_action(step["id"], command(
                action="start", expected_revision=workflow["revision"], payload={}))
            workflow = self.engine.apply_action(step["id"], command(
                action="complete", expected_revision=workflow["revision"], payload={}))
        with self.assertRaises(ConflictError):
            self.migrate(workflow, self.v2)

    def test_def9_migration_is_idempotent_on_retry(self):
        workflow = self.start(self.v1)
        once = command(target_version_id=self.v2, reason="Approved",
                       expected_revision=workflow["revision"], actor=MIGRATOR)
        first = self.engine.migrate_version(owner(workflow), once)
        second = self.engine.migrate_version(owner(workflow), once)
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(second["version_number"], 2)


if __name__ == "__main__":
    unittest.main()
