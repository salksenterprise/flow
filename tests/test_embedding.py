"""Atomic commit across domain and orchestration writes.

The central property of the fused design: an application can record a business
decision and advance its workflow in one transaction, so the two can never
disagree. That claim is worth nothing until something proves it.
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from isrp.orchestration import WorkflowEngine
from isrp.orchestration import SQLiteWorkflowRepository

from approval_app import ApprovalApp, HostFailure


class EmbeddingContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "approvals.db"
        self.app = ApprovalApp(self.path)
        self.app.migrate()

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def workflow_count(self) -> int:
        with self.app.transaction() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM isrp_request").fetchone()[0]

    def event_count(self) -> int:
        with self.app.transaction() as connection:
            return connection.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]

    # EMB-1: the host supplies the connection; Flow never opens one.

    def test_emb1_embedded_repository_refuses_to_open_a_connection(self):
        repository = SQLiteWorkflowRepository()
        with self.assertRaises(RuntimeError) as raised:
            repository.initialize()
        self.assertIn("embedded", str(raised.exception))

    def test_emb1_host_schema_and_flow_schema_share_one_database(self):
        with self.app.transaction() as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("approval_request", tables)  # the domain's
        self.assertIn("isrp_request", tables)      # the orchestrated aggregate

    # EMB-2: the host commits; Flow calls no commit or rollback.

    def test_emb2_flow_leaves_the_host_transaction_open(self):
        self.app.connection.execute("BEGIN IMMEDIATE")
        with self.app.repository.using(self.app.connection):
            self.app.engine.start_request({
                "command_id": str(uuid.uuid4()),
                "workflow_version_id": self.app.workflow_version_id,
                "title": "Uncommitted",
                "variables": {},
            })
            self.assertTrue(self.app.connection.in_transaction)
        # Still the host's to decide, even after Flow has finished with it.
        self.assertTrue(self.app.connection.in_transaction)
        self.app.connection.execute("ROLLBACK")
        self.assertEqual(self.event_count(), 0)

    def test_emb2_flow_writes_stay_invisible_until_the_host_commits(self):
        """Direct proof that Flow does not commit behind the host's back."""
        observer = sqlite3.connect(str(self.path), timeout=5.0)
        try:
            baseline = observer.execute(
                "SELECT COUNT(*) FROM isrp_request").fetchone()[0]

            self.app.connection.execute("BEGIN IMMEDIATE")
            with self.app.repository.using(self.app.connection):
                self.app.engine.start_request({
                    "command_id": str(uuid.uuid4()),
                    "workflow_version_id": self.app.workflow_version_id,
                    "title": "In flight",
                    "variables": {},
                })

            self.assertEqual(
                observer.execute("SELECT COUNT(*) FROM isrp_request").fetchone()[0],
                baseline, "orchestration committed the caller's transaction")

            self.app.connection.execute("COMMIT")
            self.assertEqual(
                observer.execute("SELECT COUNT(*) FROM isrp_request").fetchone()[0],
                baseline + 1)
        finally:
            observer.close()

    # EMB-3: a domain write and a workflow transition commit atomically.

    def test_emb3_domain_write_and_workflow_transition_commit_together(self):
        self.app.submit("REQ-1", "New laptop")
        self.app.decide("REQ-1", "APPROVED")

        snapshot = self.app.snapshot("REQ-1")
        self.assertEqual(snapshot["decision"], "APPROVED")
        self.assertEqual(snapshot["review"], "COMPLETED")
        self.assertEqual(snapshot["execution_status"], "COMPLETED")

    def test_emb3_domain_write_and_workflow_transition_roll_back_together(self):
        self.app.submit("REQ-2", "Standing desk")
        before, events_before = self.app.snapshot("REQ-2"), self.event_count()

        with self.assertRaises(HostFailure):
            self.app.decide("REQ-2", "APPROVED", then_fail=True)

        self.assertEqual(self.app.snapshot("REQ-2"), before)
        self.assertIsNone(self.app.snapshot("REQ-2")["decision"])
        self.assertEqual(self.event_count(), events_before)

    def test_emb3_a_failed_submit_leaves_no_orphan_workflow(self):
        """A workflow started inside an aborted host transaction must not survive.

        Deliberately uses a fresh command identifier, so idempotency cannot
        mask a missing rollback: the workflow really is created, and then the
        host's insert fails on the duplicate reference.
        """
        self.app.submit("REQ-3", "First")
        before = self.workflow_count()

        with self.assertRaises(sqlite3.IntegrityError):
            with self.app.transaction() as connection:
                workflow = self.app.engine.start_request({
                    "command_id": str(uuid.uuid4()),
                    "workflow_version_id": self.app.workflow_version_id,
                    "title": "Duplicate reference",
                    "actor": "test", "variables": {},
                })
                connection.execute(
                    """INSERT INTO approval_request(reference,title,request_id)
                       VALUES (?,?,?)""", ("REQ-3", "Duplicate", workflow["id"]))

        self.assertEqual(self.workflow_count(), before)

    def test_emb3_workflow_reference_is_a_real_foreign_key(self):
        """The payoff a service boundary cannot offer: the database enforces it."""
        with self.assertRaises(sqlite3.IntegrityError):
            with self.app.transaction() as connection:
                connection.execute(
                    """INSERT INTO approval_request(reference,title,request_id)
                       VALUES ('REQ-BAD','Points at nothing',987654)""")

    # EMB-9: background routines are callable functions the host schedules.

    def test_emb9_host_drives_background_work_inside_its_own_transaction(self):
        with self.app.transaction():
            self.assertEqual(self.app.engine.process_due_timers(), 0)

    # EMB-10: one repository serves concurrent host connections.

    def test_emb10_one_repository_serves_concurrent_host_connections(self):
        repository, engine = self.app.repository, self.app.engine
        version, path = self.app.workflow_version_id, str(self.path)
        started, errors, lock = [], [], threading.Lock()

        def run():
            connection = sqlite3.connect(path, isolation_level=None, timeout=30.0)
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                for _ in range(5):
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        with repository.using(connection):
                            workflow = engine.start_request({
                                "command_id": str(uuid.uuid4()),
                                "workflow_version_id": version, "title": "Concurrent",
                                "variables": {},
                            })
                        connection.execute("COMMIT")
                    except Exception as error:  # noqa: BLE001 - recorded below
                        if connection.in_transaction:
                            connection.execute("ROLLBACK")
                        with lock:
                            errors.append(repr(error))
                    else:
                        with lock:
                            started.append(workflow["id"])
            finally:
                connection.close()

        threads = [threading.Thread(target=run) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(set(started)), 20)

    def test_emb10_binding_twice_on_one_thread_is_refused(self):
        with self.app.repository.using(self.app.connection):
            with self.assertRaises(RuntimeError):
                with self.app.repository.using(self.app.connection):
                    pass

    # EMB-6: schema installs from the host's migration step, and is idempotent.

    def test_emb6_create_schema_is_idempotent_and_refuses_an_open_transaction(self):
        self.app.submit("REQ-4", "Keep me")
        self.app.repository.create_schema(self.app.connection)  # a second deploy
        self.assertEqual(self.app.snapshot("REQ-4")["review"], "READY")

        self.app.connection.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaises(RuntimeError):
                self.app.repository.create_schema(self.app.connection)
        finally:
            self.app.connection.execute("ROLLBACK")


if __name__ == "__main__":
    unittest.main()
