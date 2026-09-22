"""One event log, one outbox, and one inbox for the fused ISRP application.

Domain and orchestration changes use the same ordered aggregate stream, paired
outbox rows, and application transaction.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from isrp.orchestration import ValidationError, WorkflowEngine
from isrp.orchestration.schema import SCHEMA_VERSION
from isrp.orchestration import SQLiteWorkflowRepository

from support import owner


def command(**values):
    return {"command_id": str(uuid.uuid4()), "actor": "tester", **values}


TEMPLATE = {
    "key": "shared", "name": "Shared", "version": 1, "publish": True,
    "steps": [{"key": "work", "name": "Work", "type": "HUMAN_TASK", "configuration": {}},
              {"key": "end", "name": "End", "type": "END"}],
    "transitions": [{"from_step": "work", "to_step": "end"}],
}


class SharedLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "shared.db"
        self.repository = SQLiteWorkflowRepository(self.path)
        self.repository.initialize()
        self.engine = WorkflowEngine(self.repository)
        self.version = self.engine.import_template(TEMPLATE)["workflow_version_id"]

    def tearDown(self):
        self.temp.cleanup()

    def start(self, **values):
        data = {"workflow_version_id": self.version, "title": "T",
                "variables": {},}
        data.update(values)
        return self.engine.start_request(command(**data))

    def rows(self, sql, *args):
        with self.repository.transaction():
            return self.repository.db.execute(sql, args).fetchall()

    # Domain and execution events share one log.

    def test_host_domain_event_lands_in_the_shared_log(self):
        recorded = self.engine.record_event(
            "ISRP_ASSESSMENT", "ASMT-1002", "RESPONSE_SUBMITTED",
            actor={"actor_id": "alice", "organization_id": "security"},
            correlation_id="ISR-100", new_revision=4,
            changed_fields={"response_status": "SUBMITTED"},
            payload={"submission_id": "SUB-9"})

        self.assertEqual(recorded["sequence_number"], 1)
        stored = self.engine.list_events("ISRP_ASSESSMENT", "ASMT-1002")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["event_type"], "RESPONSE_SUBMITTED")
        self.assertEqual(stored[0]["actor"], "alice")
        self.assertEqual(stored[0]["actor_org_id"], "security")
        self.assertEqual(stored[0]["changed_fields"], {"response_status": "SUBMITTED"})

    def test_each_aggregate_has_its_own_dense_sequence(self):
        workflow = self.start()
        for index in range(3):
            self.engine.record_event("ISRP_ASSESSMENT", "ASMT-1", f"EVENT_{index}")
        self.engine.record_event("ISRP_ASSESSMENT", "ASMT-2", "OTHER")

        first = [item["sequence_number"]
                 for item in self.engine.list_events("ISRP_ASSESSMENT", "ASMT-1")]
        second = [item["sequence_number"]
                  for item in self.engine.list_events("ISRP_ASSESSMENT", "ASMT-2")]
        workflow_events = self.engine.list_events(*owner(workflow))

        self.assertEqual(first, [1, 2, 3])
        self.assertEqual(second, [1])
        self.assertEqual([item["sequence_number"] for item in workflow_events],
                         list(range(1, len(workflow_events) + 1)))

    def test_a_domain_event_is_paired_with_an_outbox_row(self):
        self.engine.record_event("ISRP_FINDING", "FND-3", "FINDING_CREATED",
                                 correlation_id="ISR-100", new_revision=2)
        row = self.rows("""SELECT event_type,aggregate_type,aggregate_id,aggregate_version,
                                  correlation_id,payload_json FROM outbox_event
                           WHERE aggregate_type='ISRP_FINDING'""")[0]
        self.assertEqual(row[0], "FINDING_CREATED")
        self.assertEqual((row[1], row[2], row[3]), ("ISRP_FINDING", "FND-3", 2))
        self.assertEqual(row[4], "ISR-100")
        self.assertEqual(json.loads(row[5])["schema_version"], 1)

    def test_an_audit_only_event_is_not_queued_for_delivery(self):
        self.engine.record_event("ISRP_EVIDENCE", "EV-1", "EVIDENCE_VIEWED", publish=False)
        self.assertEqual(len(self.engine.list_events("ISRP_EVIDENCE", "EV-1")), 1)
        self.assertEqual(
            self.rows("SELECT COUNT(*) FROM outbox_event WHERE aggregate_type='ISRP_EVIDENCE'")[0][0],
            0)

    def test_no_aggregate_type_is_reserved_away_from_the_domain(self):
        """Step 8 of the fusion: there is no separate WORKFLOW aggregate.

        Reserving one was what forced a domain event and the execution event it
        caused into two sequences. ISRP writes against the same aggregate the
        orchestration drives, and only empty identifiers are refused.
        """
        recorded = self.engine.record_event("ISRP_REQUEST", "1", "REQUEST_SUBMITTED")
        self.assertEqual(recorded["sequence_number"], 1)
        for bad in (("", "X", "T"), ("ISRP", "", "T"), ("ISRP", "X", "")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    self.engine.record_event(*bad)

    def test_an_unknown_event_field_is_refused_rather_than_dropped(self):
        with self.assertRaises(ValidationError):
            self.engine.record_event("ISRP_REQUEST", "ISR-1", "X", nonsense=True)

    # The point of fusion: one transaction over both.

    def test_a_domain_event_and_a_workflow_transition_roll_back_together(self):
        workflow = self.start()
        before = self.rows("SELECT COUNT(*) FROM event_log")[0][0]

        class Abort(Exception):
            pass

        with self.assertRaises(Abort):
            with self.repository.transaction():
                self.engine.record_event("ISRP_ASSESSMENT", "ASMT-7", "DECISION_RECORDED")
                self.engine.apply_action(workflow["steps"][0]["id"], command(
                    action="start", expected_revision=workflow["revision"], payload={}))
                raise Abort()

        self.assertEqual(self.rows("SELECT COUNT(*) FROM event_log")[0][0], before)
        self.assertEqual(self.engine.list_events("ISRP_ASSESSMENT", "ASMT-7"), [])

    # The shared inbox.

    def test_a_provider_event_is_deduplicated_without_needing_a_workflow(self):
        event = {"connector_name": "issue-manager", "provider_event_id": "evt-1",
                 "event_type": "ISSUE_RESOLVED", "correlation_id": "ISR-100",
                 "payload": {"external_issue_id": "ISS-9"}}
        first = self.engine.record_inbox_event(event)
        second = self.engine.record_inbox_event(event)

        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["inbox_event"]["correlation_id"], "ISR-100")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM inbox_event")[0][0], 1)

    def test_an_inbox_event_is_missing_required_fields(self):
        with self.assertRaises(ValidationError):
            self.engine.record_inbox_event({"connector_name": "x"})

    def test_inbox_events_are_claimed_then_completed(self):
        self.engine.record_inbox_event({
            "connector_name": "issue-manager", "provider_event_id": "evt-2",
            "event_type": "ISSUE_RESOLVED"})
        claimed = self.engine.claim_inbox_events("connector-runner")
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["claimed_by"], "connector-runner")

        self.engine.complete_inbox_event(claimed[0]["id"], "connector-runner")
        self.assertEqual(self.engine.claim_inbox_events("connector-runner"), [])
        self.assertEqual(
            self.rows("SELECT status FROM inbox_event WHERE id=?", claimed[0]["id"])[0][0],
            "PROCESSED")


class SharedLogUpgradeTests(unittest.TestCase):
    """A database stamped at version 4 must carry its history forward."""

    def test_version_four_database_migrates_workflow_event_into_event_log(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v4.db"
            # Foreign keys stay off while an older shape is synthesised.
            seed = sqlite3.connect(path)
            seed.executescript("""
                CREATE TABLE schema_metadata (
                  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);
                INSERT INTO schema_metadata(key,value) VALUES ('schema_version','4');
                CREATE TABLE workflow_event (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
                  workflow_instance_id INTEGER NOT NULL,
                  sequence_number INTEGER NOT NULL, step_instance_id INTEGER,
                  event_type TEXT NOT NULL, actor TEXT NOT NULL, actor_org_id TEXT,
                  previous_state TEXT, new_state TEXT, payload_json TEXT,
                  created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00.000Z',
                  UNIQUE(workflow_instance_id, sequence_number));
                INSERT INTO workflow_event
                  (event_id,workflow_instance_id,sequence_number,event_type,actor)
                  VALUES ('evt-a',1,1,'WORKFLOW_STARTED','legacy.user'),
                         ('evt-b',1,2,'STEP_ACTIVATED','engine');
            """)
            seed.commit()
            seed.close()

            upgrade = sqlite3.connect(path, isolation_level=None)
            SQLiteWorkflowRepository(path).create_schema(upgrade)

            carried = upgrade.execute(
                """SELECT event_id,aggregate_type,aggregate_id,sequence_number
                   FROM event_log ORDER BY sequence_number""").fetchall()
            tables = {row[0] for row in upgrade.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            stamped = upgrade.execute(
                "SELECT value FROM schema_metadata WHERE key='schema_version'").fetchone()[0]
            upgrade.close()

            self.assertEqual(carried, [("evt-a", "ISRP_REQUEST", "1", 1),
                                       ("evt-b", "ISRP_REQUEST", "1", 2)])
            self.assertNotIn("workflow_event", tables, "the old table must be removed")
            self.assertEqual(stamped, str(SCHEMA_VERSION),
                             "the chain must carry an old database to the current version")


if __name__ == "__main__":
    unittest.main()
