"""Regression tests for the P0 defects recorded in docs/requirements.md.

Each test cites the requirement identifier it covers. Per docs/definition-of-done.md
rule 3, every one of these failed before the corresponding fix was written.
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from workflow_core import ConflictError, WorkflowEngine
from workflow_sqlite import SQLiteWorkflowRepository


def command(**values):
    return {"command_id": str(uuid.uuid4()), "actor": "tester", **values}


def linear_template(key: str, step_type: str = "HUMAN_TASK", configuration=None, **extra):
    template = {
        "key": key, "name": key, "version": 1, "publish": True,
        "steps": [
            {"key": "work", "name": "Work", "type": step_type,
             "configuration": configuration or {}},
            {"key": "end", "name": "End", "type": "END"},
        ],
        "transitions": [{"from_step": "work", "to_step": "end"}],
    }
    template.update(extra)
    return template


PERMISSIONED_LIFECYCLE = {
    "key": "test.permissioned-lifecycle", "name": "Permissioned", "version": 1,
    "initial_state": "OPEN",
    "states": ["OPEN", {"key": "CLOSED", "terminal": True}],
    "transitions": [
        {"action": "close", "from": "OPEN", "to": "CLOSED",
         "required_permission": "workflow.close"},
    ],
}

CUSTOM_STEP_FSM = {
    "key": "test.custom-step", "name": "Custom step", "version": 1,
    "initial_state": "NOT_READY",
    "states": ["NOT_READY", "READY", {"key": "DONE", "terminal": True}],
    "transitions": [
        {"action": "activate", "from": "NOT_READY", "to": "READY"},
        {"action": "finish", "from": "READY", "to": "DONE"},
    ],
}


class P0DefectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = SQLiteWorkflowRepository(Path(self.temp.name) / "workflow.db")
        self.repository.initialize()
        self.engine = WorkflowEngine(self.repository)

    def tearDown(self):
        self.temp.cleanup()

    def import_template(self, template):
        return self.engine.import_template(template)["workflow_version_id"]

    def start(self, version_id, **values):
        data = {
            "workflow_version_id": version_id, "title": "Test workflow",
            "business_type": "TEST", "business_key": str(uuid.uuid4()),
            "variables": {}, "subjects": [],
        }
        data.update(values)
        return self.engine.start_workflow(command(**data))

    def act(self, workflow, key, action, **values):
        step = next(item for item in workflow["steps"] if item["step_key"] == key)
        return self.engine.apply_action(step["id"], command(
            action=action, expected_revision=workflow["revision"], payload={}, **values))

    def execution(self, workflow):
        return {item["step_key"]: item["execution_status"] for item in workflow["steps"]}

    # EXE-3: a transition may require a permission, and is refused without it.

    def test_exe3_permissioned_transition_refuses_actor_without_permission(self):
        version = self.import_template(
            linear_template("exe3-deny", lifecycle_fsm=PERMISSIONED_LIFECYCLE))
        workflow = self.start(version)
        with self.assertRaises(ConflictError):
            self.engine.apply_workflow_action(workflow["id"], command(
                action="close", expected_revision=workflow["revision"],
                actor={"actor_id": "mallory", "permissions": []}))

    def test_exe3_permissioned_transition_allows_actor_with_permission(self):
        version = self.import_template(
            linear_template("exe3-allow", lifecycle_fsm=PERMISSIONED_LIFECYCLE))
        workflow = self.start(version)
        result = self.engine.apply_workflow_action(workflow["id"], command(
            action="close", expected_revision=workflow["revision"],
            actor={"actor_id": "alice", "permissions": ["workflow.close"]}))
        self.assertEqual(result["lifecycle_state"], "CLOSED")

    # REL-10 / EMB-7: restarting a process alters no workflow or node state.

    def test_rel10_restart_preserves_custom_fsm_step_state(self):
        template = linear_template("rel10-step")
        template["steps"][0]["fsm"] = CUSTOM_STEP_FSM
        template["steps"].insert(
            1, {"key": "second", "name": "Second", "type": "HUMAN_TASK", "configuration": {}})
        template["transitions"] = [
            {"from_step": "work", "to_step": "second"},
            {"from_step": "second", "to_step": "end"},
        ]
        workflow = self.start(self.import_template(template))
        workflow = self.act(workflow, "work", "finish")
        self.assertEqual(self.execution(workflow)["work"], "COMPLETED")

        self.repository.initialize()  # every API and worker process start does this

        after = self.engine.get_workflow(workflow["id"])
        self.assertEqual(self.execution(after)["work"], "COMPLETED")

    def test_rel10_restart_preserves_completed_custom_lifecycle_state(self):
        lifecycle = {
            "key": "test.rel10-lifecycle", "name": "Archive lifecycle", "version": 1,
            "initial_state": "OPEN",
            "states": ["OPEN", {"key": "ARCHIVED", "terminal": True}],
            "transitions": [{"action": "complete", "from": "OPEN", "to": "ARCHIVED"}],
        }
        version = self.import_template(
            linear_template("rel10-lifecycle", lifecycle_fsm=lifecycle))
        workflow = self.start(version)
        workflow = self.act(workflow, "work", "start")
        workflow = self.act(workflow, "work", "complete")
        self.assertEqual(workflow["lifecycle_state"], "ARCHIVED")

        self.repository.initialize()

        self.assertEqual(
            self.engine.get_workflow(workflow["id"])["lifecycle_state"], "ARCHIVED")

    def test_emb7_legacy_database_migrates_once_and_then_stops(self):
        """A 0.2 database still upgrades, and the migration never runs again."""
        path = Path(self.temp.name) / "legacy.db"
        connection = sqlite3.connect(path)
        connection.executescript("""
            CREATE TABLE workflow_instance (
              id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_version_id INTEGER NOT NULL,
              title TEXT NOT NULL, business_type TEXT, business_key TEXT,
              correlation_id TEXT, status TEXT NOT NULL DEFAULT 'ACTIVE',
              current_stage TEXT, revision INTEGER NOT NULL DEFAULT 0,
              variables_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT);
            INSERT INTO workflow_instance
              (workflow_version_id,title,status,revision,created_by)
              VALUES (1,'Legacy run','COMPLETED',3,'legacy.user');
        """)
        connection.commit()
        connection.close()

        repository = SQLiteWorkflowRepository(path)
        repository.initialize()
        with repository.transaction():
            migrated = dict(repository.db.execute(
                "SELECT lifecycle_state,execution_status FROM workflow_instance").fetchone())
            stamped = repository.db.execute(
                "SELECT value FROM schema_metadata WHERE key='schema_version'").fetchone()
        self.assertEqual(migrated["execution_status"], "COMPLETED")
        self.assertEqual(migrated["lifecycle_state"], "COMPLETED")
        self.assertIsNotNone(stamped)

        repository.initialize()  # a second process start must change nothing
        with repository.transaction():
            self.assertEqual(repository.db.execute(
                "SELECT COUNT(*) FROM workflow_instance").fetchone()[0], 1)

    # EMB-10: the core is safe to use concurrently from a multi-threaded host.

    def test_emb10_repository_supports_concurrent_callers(self):
        version = self.import_template(linear_template("emb10"))
        errors, started, lock = [], [], threading.Lock()

        def run():
            for _ in range(10):
                try:
                    workflow = self.start(version)
                except Exception as error:  # noqa: BLE001 - recorded for the assertion
                    with lock:
                        errors.append(repr(error))
                else:
                    with lock:
                        started.append(workflow["id"])

        threads = [threading.Thread(target=run) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(set(started)), 40)

    # EXE-11: terminating cancels open nodes, timers, jobs and children.

    def test_exe11_terminate_cancels_open_work_and_reports_cancelled(self):
        version = self.import_template(
            linear_template("exe11", "TIMER", {"delay_seconds": 3600}))
        workflow = self.start(version)
        workflow = self.engine.apply_workflow_action(workflow["id"], command(
            action="terminate", reason="No longer required",
            expected_revision=workflow["revision"]))

        self.assertEqual(workflow["execution_status"], "CANCELLED")
        self.assertEqual(workflow["status"], "CANCELLED")
        self.assertEqual(self.execution(workflow)["work"], "CANCELLED")
        with self.repository.transaction():
            status = self.repository.db.execute(
                "SELECT status FROM durable_timer WHERE workflow_instance_id=?",
                (workflow["id"],)).fetchone()[0]
        self.assertEqual(status, "CANCELLED")

    def test_exe11_terminate_cascades_to_running_children(self):
        child_version = self.import_template(linear_template("exe11-child"))
        parent_version = self.import_template({
            "key": "exe11-parent", "name": "Parent", "version": 1, "publish": True,
            "steps": [{"key": "sub", "name": "Sub", "type": "SUBWORKFLOW", "configuration": {}},
                      {"key": "end", "name": "End", "type": "END"}],
            "transitions": [{"from_step": "sub", "to_step": "end"}],
        })
        parent = self.start(parent_version)
        sub = next(item for item in parent["steps"] if item["step_key"] == "sub")
        child = self.engine.start_child_workflow(parent["id"], command(
            workflow_version_id=child_version, title="Child", variables={}, subjects=[],
            parent_step_instance_id=sub["id"], expected_revision=parent["revision"]))

        parent = self.engine.get_workflow(parent["id"])
        self.engine.apply_workflow_action(parent["id"], command(
            action="terminate", reason="Parent abandoned",
            expected_revision=parent["revision"]))

        self.assertEqual(
            self.engine.get_workflow(child["id"])["execution_status"], "CANCELLED")

    # EXE-12: a suspended workflow performs no activation, firing or completion.

    def test_exe12_suspended_workflow_does_not_fire_timers(self):
        version = self.import_template(
            linear_template("exe12", "TIMER", {"delay_seconds": 0}))
        workflow = self.start(version)
        workflow = self.engine.apply_workflow_action(workflow["id"], command(
            action="suspend", expected_revision=workflow["revision"]))

        self.assertEqual(self.engine.process_due_timers(), 0)
        suspended = self.engine.get_workflow(workflow["id"])
        self.assertEqual(suspended["execution_status"], "SUSPENDED")
        self.assertEqual(self.execution(suspended)["work"], "WAITING")

        self.engine.apply_workflow_action(workflow["id"], command(
            action="resume", expected_revision=suspended["revision"]))
        self.assertEqual(self.engine.process_due_timers(), 1)
        self.assertEqual(
            self.engine.get_workflow(workflow["id"])["execution_status"], "COMPLETED")

    def test_exe12_suspended_workflow_does_not_release_automation_jobs(self):
        version = self.import_template(
            linear_template("exe12-job", "AUTOMATED_TASK", {"handler": "noop"}))
        workflow = self.start(version)
        self.engine.apply_workflow_action(workflow["id"], command(
            action="suspend", expected_revision=workflow["revision"]))
        self.assertEqual(self.engine.claim_automation_jobs("worker-1"), [])

    # REL-2: idempotency is checked before revision validation.

    def test_rel2_child_workflow_start_is_idempotent_on_retry(self):
        child_version = self.import_template(linear_template("rel2-child"))
        parent_version = self.import_template({
            "key": "rel2-parent", "name": "Parent", "version": 1, "publish": True,
            "steps": [{"key": "sub", "name": "Sub", "type": "SUBWORKFLOW", "configuration": {}},
                      {"key": "end", "name": "End", "type": "END"}],
            "transitions": [{"from_step": "sub", "to_step": "end"}],
        })
        parent = self.start(parent_version)
        sub = next(item for item in parent["steps"] if item["step_key"] == "sub")
        child_command = command(
            workflow_version_id=child_version, title="Child", variables={}, subjects=[],
            parent_step_instance_id=sub["id"], expected_revision=parent["revision"])

        first = self.engine.start_child_workflow(parent["id"], child_command)
        second = self.engine.start_child_workflow(parent["id"], child_command)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.engine.get_workflow(parent["id"])["children"]), 1)

    # REL-7: retry redelivers only to subscriptions that have not yet succeeded.

    def test_rel7_retry_skips_subscriptions_that_already_succeeded(self):
        good = self.repository.create_subscription(
            {"name": "good", "target_url": "https://example.test/good", "event_types": []})
        bad = self.repository.create_subscription(
            {"name": "bad", "target_url": "https://example.test/bad", "event_types": []})
        self.start(self.import_template(linear_template("rel7")))
        with self.repository.transaction():
            outbox_id = self.repository.db.execute(
                "SELECT id FROM outbox_event ORDER BY id LIMIT 1").fetchone()[0]

        self.repository.record_delivery(outbox_id, good["id"], True)
        self.repository.record_delivery(outbox_id, bad["id"], False, "HTTP 500")

        self.assertEqual(
            self.repository.delivered_subscription_ids(outbox_id), {good["id"]})

    # OPS-5: delivery secrets are never returned by a read interface.

    def test_ops5_subscription_reads_do_not_expose_secrets(self):
        created = self.repository.create_subscription({
            "name": "signed", "target_url": "https://example.test/hook",
            "event_types": [], "secret": "super-secret-value"})
        self.assertNotIn("super-secret-value", repr(created))
        self.assertNotIn("super-secret-value", repr(self.repository.list_subscriptions()))

        internal = self.repository.list_subscriptions(active_only=True, include_secrets=True)
        self.assertEqual(internal[0]["secret"], "super-secret-value")

    # JOB-3: two workers cannot hold the same job simultaneously.

    def test_job3_concurrent_workers_do_not_claim_the_same_job(self):
        version = self.import_template(
            linear_template("job3", "AUTOMATED_TASK", {"handler": "noop"}))
        self.start(version)
        claims, lock = [], threading.Lock()

        def run(worker_id):
            jobs = self.engine.claim_automation_jobs(worker_id, limit=10)
            with lock:
                claims.extend(job["id"] for job in jobs)

        threads = [threading.Thread(target=run, args=(f"worker-{index}",))
                   for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(claims), len(set(claims)))
        self.assertEqual(len(claims), 1)


if __name__ == "__main__":
    unittest.main()
