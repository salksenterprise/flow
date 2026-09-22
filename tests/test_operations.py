"""Reliability and operations: docs/requirements.md sections F, G and H."""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from isrp.orchestration import ConflictError, ValidationError, WorkflowEngine
from isrp.orchestration import SQLiteWorkflowRepository

from support import aggregate_args, owner


def command(**values):
    return {"command_id": str(uuid.uuid4()), "actor": "tester", **values}


def linear(key, step_type="HUMAN_TASK", configuration=None):
    return {
        "key": key, "name": key, "version": 1, "publish": True,
        "steps": [
            {"key": "work", "name": "Work", "type": step_type,
             "configuration": configuration or {}},
            {"key": "end", "name": "End", "type": "END"},
        ],
        "transitions": [{"from_step": "work", "to_step": "end"}],
    }


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = SQLiteWorkflowRepository(Path(self.temp.name) / "workflow.db")
        self.repository.initialize()
        self.engine = WorkflowEngine(self.repository)

    def tearDown(self):
        self.temp.cleanup()

    def start(self, template, **values):
        version = self.engine.import_template(template)["workflow_version_id"]
        data = {"workflow_version_id": version, "title": "T", "variables": {},}
        data.update(values)
        return self.engine.start_request(command(**data))

    def act(self, workflow, key, action, **values):
        step = next(item for item in workflow["steps"] if item["step_key"] == key)
        return self.engine.apply_action(step["id"], command(
            action=action, expected_revision=workflow["revision"], payload={}, **values))

    def step_id(self, workflow, key):
        return next(item["id"] for item in workflow["steps"] if item["step_key"] == key)

    def query(self, sql, *args):
        with self.repository.transaction():
            return self.repository.db.execute(sql, args).fetchall()

    # OPS-7: command results are receipts, not snapshots.

    def test_ops7_command_storage_does_not_grow_with_workflow_history(self):
        workflow = self.start(linear("ops7"))
        workflow = self.act(workflow, "work", "start")
        self.act(workflow, "work", "complete")

        stored = [dict(row) for row in self.query(
            """SELECT command_id,owner_type,owner_id,action,revision,processed_at
               FROM workflow_command""")]
        self.assertGreaterEqual(len(stored), 3)
        for receipt in stored:
            # Columns, not a serialized copy of the response. A receipt says
            # which aggregate the command hit and what revision it produced;
            # there is nothing in it that can grow with that aggregate's
            # history.
            self.assertEqual(receipt["owner_type"], "ISRP_REQUEST")
            self.assertIsNotNone(receipt["action"])

    def test_ops7_replaying_a_command_returns_current_state_and_has_no_second_effect(self):
        workflow = self.start(linear("ops7-replay"))
        step = self.step_id(workflow, "work")
        once = command(action="start", expected_revision=workflow["revision"], payload={})

        first = self.engine.apply_action(step, once)
        second = self.engine.apply_action(step, once)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["revision"], second["revision"])
        starts = self.query(
            "SELECT COUNT(*) FROM event_log WHERE event_type='STEP_START'")
        self.assertEqual(starts[0][0], 1)

    # REL-8: outbound messages carry a schema version.

    def test_rel8_outbound_messages_carry_a_schema_version(self):
        self.start(linear("rel8"))
        payload = json.loads(self.query(
            "SELECT payload_json FROM outbox_event ORDER BY id LIMIT 1")[0][0])
        self.assertEqual(payload["schema_version"], 1)

    # JOB-2: an expired lease permits reclaim.

    def test_job2_expired_lease_permits_reclaim(self):
        self.start(linear("job2", "AUTOMATED_TASK", {"handler": "noop"}))
        first = self.engine.claim_automation_jobs("worker-1")
        self.assertEqual(len(first), 1)
        self.assertEqual(self.engine.claim_automation_jobs("worker-2"), [],
                         "a live lease must not be stealable")

        with self.repository.transaction():
            self.repository.db.execute(
                "UPDATE automation_job SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (first[0]["id"],))

        second = self.engine.claim_automation_jobs("worker-2")
        self.assertEqual([job["id"] for job in second], [first[0]["id"]])
        self.assertEqual(second[0]["claimed_by"], "worker-2")

    # OPS-2: a wedged workflow is detectable.

    def test_ops2_detects_a_workflow_with_nothing_left_to_run(self):
        workflow = self.start(linear("ops2"))
        self.assertEqual(self.engine.stuck_aggregates(), [])

        # The engine should never produce this state. The detector exists for
        # when a bug or a crash does, because nothing else reports it.
        with self.repository.transaction():
            self.repository.db.execute(
                """UPDATE step_instance SET execution_status='COMPLETED'
                   WHERE owner_type=? AND owner_id=?""", owner(workflow))

        self.assertEqual(
            [item["id"] for item in self.engine.stuck_aggregates()], [workflow["id"]])

    # OPS-3: repair is authorized, reasoned and audited.

    def test_ops3_repair_requires_permission_and_a_reason(self):
        workflow = self.start(linear("ops3-auth"))
        step = self.step_id(workflow, "work")
        with self.assertRaises(ConflictError):
            self.engine.repair_step(step, command(
                action="skip_step", reason="no permission held",
                expected_revision=workflow["revision"],
                actor={"actor_id": "mallory", "permissions": []}))
        with self.assertRaises(ValidationError):
            self.engine.repair_step(step, command(
                action="skip_step", expected_revision=workflow["revision"],
                actor={"actor_id": "ops", "permissions": ["workflow.repair"]}))

    def test_ops3_skip_step_releases_a_blocked_workflow(self):
        workflow = self.start(linear("ops3-skip"))
        result = self.engine.repair_step(self.step_id(workflow, "work"), command(
            action="skip_step", reason="Vendor never responded; proceeding",
            expected_revision=workflow["revision"],
            actor={"actor_id": "ops", "permissions": ["workflow.repair"]}))
        self.assertEqual(result["execution_status"], "COMPLETED")
        events = [row[0] for row in self.query(
            "SELECT event_type FROM event_log WHERE aggregate_type=? AND aggregate_id=?",
            *aggregate_args(result))]
        self.assertIn("REPAIR_SKIP_STEP", events)

    def test_ops3_retry_step_revives_a_failed_workflow(self):
        workflow = self.start(linear("ops3-retry"))
        workflow = self.act(workflow, "work", "start")
        workflow = self.act(workflow, "work", "fail")
        self.assertEqual(workflow["execution_status"], "FAILED")

        revived = self.engine.repair_step(self.step_id(workflow, "work"), command(
            action="retry_step", reason="Upstream outage cleared",
            expected_revision=workflow["revision"],
            actor={"actor_id": "ops", "permissions": ["workflow.repair"]}))

        self.assertEqual(revived["execution_status"], "RUNNING")
        self.assertEqual(
            {item["step_key"]: item["execution_status"] for item in revived["steps"]}["work"],
            "READY")

    def test_ops3_reassign_step_records_the_new_owner(self):
        workflow = self.start(linear("ops3-reassign"))
        result = self.engine.repair_step(self.step_id(workflow, "work"), command(
            action="reassign_step", assignee="alice", reason="Original owner left",
            expected_revision=workflow["revision"],
            actor={"actor_id": "ops", "permissions": ["workflow.repair"]}))
        assignments = next(item for item in result["steps"]
                           if item["step_key"] == "work")["assignments"]
        self.assertEqual(
            next(item["assignee"] for item in assignments if item["status"] == "OPEN"),
            "alice")

    def test_ops3_unknown_repair_action_is_refused(self):
        workflow = self.start(linear("ops3-unknown"))
        with self.assertRaises(ValidationError):
            self.engine.repair_step(self.step_id(workflow, "work"), command(
                action="delete_everything", reason="no",
                expected_revision=workflow["revision"],
                actor={"actor_id": "ops", "permissions": ["workflow.repair"]}))

    # OPS-4: dead letters are visible and redrivable.

    def test_ops4_dead_lettered_event_can_be_inspected_and_redriven(self):
        self.start(linear("ops4"))
        outbox_id = self.query("SELECT id FROM outbox_event ORDER BY id LIMIT 1")[0][0]
        with self.repository.transaction():
            self.repository.db.execute(
                "UPDATE outbox_event SET attempts=max_attempts-1 WHERE id=?", (outbox_id,))
        self.repository.mark_outbox(outbox_id, False, "connection refused")

        dead = self.engine.dead_letter_events()
        self.assertEqual([item["id"] for item in dead], [outbox_id])
        self.assertEqual(dead[0]["last_error"], "connection refused")

        self.assertTrue(self.engine.redrive_event(outbox_id))
        self.assertEqual(self.engine.dead_letter_events(), [])
        self.assertFalse(self.engine.redrive_event(outbox_id),
                         "redriving twice must not resurrect a live event")

    # OPS-6: operational counters.

    def test_ops6_counters_report_queue_and_failure_depth(self):
        self.start(linear("ops6-a"))
        self.start(linear("ops6-b", "AUTOMATED_TASK", {"handler": "noop"}))
        failing = self.start(linear("ops6-c"))
        failing = self.act(failing, "work", "start")
        self.act(failing, "work", "fail")

        counters = self.engine.operational_counters()
        self.assertEqual(counters["aggregates_failed"], 1)
        self.assertEqual(counters["jobs_queued"], 1)
        self.assertGreaterEqual(counters["aggregates_running"], 1)
        self.assertEqual(counters["outbox_dead_letter"], 0)
        for key in ("timers_due", "inbox_failed", "jobs_lease_expired", "aggregates_stuck"):
            self.assertIn(key, counters)


if __name__ == "__main__":
    unittest.main()
