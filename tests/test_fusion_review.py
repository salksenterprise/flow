"""Regression coverage for defects found during the post-fusion review."""

from __future__ import annotations

import re
import sqlite3
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from isrp.orchestration import ConflictError, ValidationError, WorkflowEngine
from isrp.orchestration import SQLiteWorkflowRepository
from isrp.orchestration.schema import SCHEMA, SCHEMA_VERSION

from support import owner


TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def command(**values):
    return {"command_id": str(uuid.uuid4()), "actor": "review", **values}


def template(key: str, step_type: str = "HUMAN_TASK", configuration=None):
    return {
        "key": key, "name": key, "version": 1, "publish": True,
        "steps": [
            {"key": "work", "name": "Work", "type": step_type,
             "configuration": configuration or {}},
            {"key": "end", "name": "End", "type": "END"},
        ],
        "transitions": [{"from_step": "work", "to_step": "end"}],
    }


class FusionReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "isrp.db"
        self.repository = SQLiteWorkflowRepository(self.path)
        self.repository.initialize()
        self.engine = WorkflowEngine(self.repository)

    def tearDown(self):
        self.temp.cleanup()

    def start(self, definition, **values):
        version = self.engine.import_template(definition)["workflow_version_id"]
        payload = {"workflow_version_id": version, "title": "Review", "variables": {}}
        payload.update(values)
        return self.engine.start_request(command(**payload))

    def scalar(self, sql, *args):
        with self.repository.transaction():
            return self.repository.db.execute(sql, args).fetchone()[0]

    def test_command_id_is_bound_to_start_payload(self):
        first_version = self.engine.import_template(template("idempotency-a"))[
            "workflow_version_id"]
        second_version = self.engine.import_template(template("idempotency-b"))[
            "workflow_version_id"]
        command_id = str(uuid.uuid4())
        self.engine.start_request({
            "command_id": command_id, "actor": "review",
            "workflow_version_id": first_version, "title": "A", "variables": {}})

        with self.assertRaises(ConflictError):
            self.engine.start_request({
                "command_id": command_id, "actor": "review",
                "workflow_version_id": second_version, "title": "B", "variables": {}})

    def test_command_id_must_not_be_empty(self):
        version = self.engine.import_template(template("empty-command"))[
            "workflow_version_id"]
        with self.assertRaises(ValidationError):
            self.engine.start_request({
                "command_id": "", "actor": "review", "workflow_version_id": version,
                "title": "Empty", "variables": {}})

    def test_command_id_is_bound_to_step_target_and_payload(self):
        workflow = self.start(template("idempotency-step"))
        step = workflow["steps"][0]
        first = {
            "command_id": str(uuid.uuid4()), "actor": "review", "action": "start",
            "expected_revision": workflow["revision"], "payload": {"source": "one"}}
        self.engine.apply_action(step["id"], first)

        with self.assertRaises(ConflictError):
            self.engine.apply_action(step["id"], {**first, "payload": {"source": "two"}})

    def test_negative_or_excessive_collection_bounds_are_rejected(self):
        workflow = self.start(template("bounded-queries"))
        for call in (
            lambda: self.engine.list_work(limit=-1),
            lambda: self.engine.list_work(limit=1001),
            lambda: self.engine.list_work(offset=-1),
            lambda: self.engine.list_events(*owner(workflow), limit=0),
            lambda: self.engine.claim_automation_jobs("worker", limit=-1),
            lambda: self.engine.claim_inbox_events("worker", limit=0),
            lambda: self.engine.dead_letter_events(limit=-1),
        ):
            with self.subTest(call=call):
                with self.assertRaises(ValidationError):
                    call()

    def test_inbox_receipt_is_leased_to_one_worker(self):
        self.engine.record_inbox_event({
            "connector_name": "issues", "provider_event_id": "evt-1",
            "event_type": "ISSUE_UPDATED"})
        claims = []
        lock = threading.Lock()

        def claim(worker):
            result = self.engine.claim_inbox_events(worker)
            with lock:
                claims.extend((worker, item["id"]) for item in result)

        threads = [threading.Thread(target=claim, args=(f"worker-{index}",))
                   for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(claims), 1)
        self.assertEqual(self.engine.claim_inbox_events("late-worker"), [])

    def test_stale_inbox_lease_is_recoverable(self):
        self.engine.record_inbox_event({
            "connector_name": "issues", "provider_event_id": "evt-2",
            "event_type": "ISSUE_UPDATED"})
        first = self.engine.claim_inbox_events("worker-1")
        with self.repository.transaction():
            self.repository.db.execute(
                "UPDATE inbox_event SET claimed_at=? WHERE id=?",
                ("2000-01-01T00:00:00.000Z", first[0]["id"]))
        second = self.engine.claim_inbox_events("worker-2")
        self.assertEqual([item["id"] for item in second], [first[0]["id"]])
        self.assertEqual(second[0]["claimed_by"], "worker-2")
        with self.assertRaises(ConflictError):
            self.engine.complete_inbox_event(first[0]["id"], "worker-1")
        self.engine.complete_inbox_event(first[0]["id"], "worker-2")

    def test_reclaimed_job_rejects_the_stale_worker_completion(self):
        self.start(template("job-lease-owner", "AUTOMATED_TASK", {"handler": "noop"}))
        first = self.engine.claim_automation_jobs("worker-1")[0]
        with self.repository.transaction():
            self.repository.db.execute(
                "UPDATE automation_job SET lease_expires_at=? WHERE id=?",
                ("2000-01-01T00:00:00.000Z", first["id"]))
        second = self.engine.claim_automation_jobs("worker-2")[0]

        with self.assertRaises(ConflictError):
            self.engine.complete_automation_job(first["id"], command(
                worker_id="worker-1", success=True))
        completed = self.engine.complete_automation_job(second["id"], command(
            worker_id="worker-2", success=True))
        self.assertEqual(completed["execution_status"], "COMPLETED")

    def test_all_generated_worker_timestamps_use_canonical_utc_shape(self):
        workflow = self.start(template(
            "timestamp-job", "AUTOMATED_TASK", {"handler": "noop", "max_attempts": 2}))
        job = self.engine.claim_automation_jobs("worker")[0]
        self.assertRegex(job["claimed_at"], TIMESTAMP)
        self.assertRegex(job["lease_expires_at"], TIMESTAMP)

        self.engine.complete_automation_job(job["id"], command(
            worker_id="worker", success=False, error="retry", retry_after_seconds=1))
        with self.repository.transaction():
            retry_at = self.repository.db.execute(
                "SELECT available_at FROM automation_job WHERE id=?", (job["id"],)
            ).fetchone()[0]
        self.assertRegex(retry_at, TIMESTAMP)

        outbox = self.repository.pending_outbox(limit=1)[0]
        self.repository.mark_outbox(outbox["id"], False, "retry")
        with self.repository.transaction():
            next_at = self.repository.db.execute(
                "SELECT next_attempt_at FROM outbox_event WHERE id=?", (outbox["id"],)
            ).fetchone()[0]
        self.assertRegex(next_at, TIMESTAMP)

    def test_timer_processing_advances_the_owner_revision(self):
        workflow = self.start(template("timer-revision", "TIMER", {"delay_seconds": 0}))
        before = workflow["revision"]
        self.assertEqual(self.engine.process_due_timers(), 1)
        self.assertGreater(self.engine.get_aggregate(owner(workflow))["revision"], before)

    def test_completed_aggregate_cannot_be_relabelled_cancelled(self):
        workflow = self.start(template("terminal-lifecycle"))
        step = workflow["steps"][0]
        workflow = self.engine.apply_action(step["id"], command(
            action="start", expected_revision=workflow["revision"], payload={}))
        workflow = self.engine.apply_action(step["id"], command(
            action="complete", expected_revision=workflow["revision"], payload={}))
        with self.assertRaises(ConflictError):
            self.engine.apply_lifecycle_action(owner(workflow), command(
                action="terminate", expected_revision=workflow["revision"],
                reason="too late"))

    def test_assignment_action_requires_a_valid_assignee(self):
        workflow = self.start(template("assignment-input"))
        step = workflow["steps"][0]
        with self.assertRaises(ValidationError):
            self.engine.apply_action(step["id"], command(
                action="assign", expected_revision=workflow["revision"], payload={}))
        with self.assertRaises(ValidationError):
            self.engine.apply_action(step["id"], command(
                action="assign", assignee="alice", assignee_type="ALIEN",
                expected_revision=workflow["revision"], payload={}))

    def test_assessment_completion_advances_the_parent_request_revision(self):
        assessment_version = self.engine.import_template(template("child-revision"))[
            "workflow_version_id"]
        request = self.start({
            "key": "parent-revision", "name": "Parent", "version": 1, "publish": True,
            "steps": [
                {"key": "assessment", "name": "Assessment", "type": "ASSESSMENT",
                 "configuration": {
                     "assessment_workflow_version_id": assessment_version}},
                {"key": "end", "name": "End", "type": "END"},
            ],
            "transitions": [{"from_step": "assessment", "to_step": "end"}],
        })
        assessment = self.engine.get_assessment(request["assessments"][0]["id"])
        step = assessment["steps"][0]
        assessment = self.engine.apply_action(step["id"], command(
            action="start", expected_revision=assessment["revision"], payload={}))
        parent_before = self.engine.get_request(request["id"])

        self.engine.apply_action(step["id"], command(
            action="complete", expected_revision=assessment["revision"], payload={}))
        parent_after = self.engine.get_request(request["id"])

        self.assertEqual(parent_after["execution_status"], "COMPLETED")
        self.assertGreater(parent_after["revision"], parent_before["revision"])

    def test_newer_database_version_is_rejected_without_restamping(self):
        future = SCHEMA_VERSION + 1
        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE schema_metadata SET value=? WHERE key='schema_version'", (str(future),))
        connection.commit()
        connection.close()

        with self.assertRaises(RuntimeError):
            self.repository.initialize()

        verify = sqlite3.connect(self.path)
        stored = verify.execute(
            "SELECT value FROM schema_metadata WHERE key='schema_version'").fetchone()[0]
        verify.close()
        self.assertEqual(stored, str(future))

    def test_version_three_database_receives_the_missing_category_migration(self):
        path = Path(self.temp.name) / "v3.db"
        connection = sqlite3.connect(path)
        version_three_schema = SCHEMA.replace(
            "state_key TEXT NOT NULL, terminal INTEGER NOT NULL DEFAULT 0, category TEXT,",
            "state_key TEXT NOT NULL, terminal INTEGER NOT NULL DEFAULT 0,",
        )
        connection.executescript(version_three_schema)
        connection.execute(
            "INSERT INTO schema_metadata(key,value) VALUES ('schema_version','3')")
        connection.commit()
        connection.close()

        SQLiteWorkflowRepository(path).initialize()

        verify = sqlite3.connect(path)
        columns = {row[1] for row in verify.execute(
            "PRAGMA table_info(fsm_state_definition)")}
        version = verify.execute(
            "SELECT value FROM schema_metadata WHERE key='schema_version'").fetchone()[0]
        verify.close()
        self.assertIn("category", columns)
        self.assertEqual(version, str(SCHEMA_VERSION))

    def test_invalid_prefix_and_definition_values_are_rejected(self):
        with self.assertRaises(ValidationError):
            SQLiteWorkflowRepository(self.path, table_prefix="bad-prefix;")

        invalid = (
            template("bad-timer", "TIMER", {"delay_seconds": -1}),
            template("bad-job", "AUTOMATED_TASK", {"max_attempts": 0}),
            template("bad-candidate", configuration={
                "candidates": [{"type": "UNKNOWN", "value": "x"}]}),
            {
                "key": "bad-quorum", "name": "Bad quorum", "version": 1,
                "publish": True,
                "steps": [
                    {"key": "work", "name": "Work", "type": "HUMAN_TASK"},
                    {"key": "join", "name": "Join", "type": "JOIN",
                     "join_rule": "N_OF_M", "configuration": {"required_count": 2}},
                    {"key": "end", "name": "End", "type": "END"},
                ],
                "transitions": [
                    {"from_step": "work", "to_step": "join"},
                    {"from_step": "join", "to_step": "end"},
                ],
            },
        )
        for definition in invalid:
            with self.subTest(key=definition["key"]):
                with self.assertRaises(ValidationError):
                    self.engine.import_template(definition)


if __name__ == "__main__":
    unittest.main()
