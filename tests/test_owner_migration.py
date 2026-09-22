"""Version 7: folding workflow_instance into the two ISRP aggregates.

The largest step of the fusion is the one that rewrites live rows, so it gets
its own file. A version 6 database is synthesised in the shape that release
actually wrote, upgraded, and then read back: a run with no parent must become
a request, a run with a parent must become an assessment of it, and every
runtime row must still point at the same work it pointed at before.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from isrp.orchestration import SQLiteWorkflowRepository
from isrp.orchestration.schema import SCHEMA_VERSION


# Only the tables version 7 rewrites, in the shape version 6 left them.
VERSION_SIX = """
CREATE TABLE schema_metadata (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);
INSERT INTO schema_metadata(key,value) VALUES ('schema_version','6');

CREATE TABLE workflow_instance (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_version_id INTEGER NOT NULL,
  title TEXT NOT NULL, business_type TEXT, business_key TEXT, correlation_id TEXT,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  lifecycle_state TEXT NOT NULL, execution_status TEXT NOT NULL DEFAULT 'RUNNING',
  current_stage TEXT, revision INTEGER NOT NULL DEFAULT 0,
  variables_json TEXT NOT NULL DEFAULT '{}',
  parent_workflow_instance_id INTEGER, root_workflow_instance_id INTEGER,
  parent_step_instance_id INTEGER, relationship_type TEXT, relationship_key TEXT,
  required_flag INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00.000Z',
  completed_at TEXT, suspended_at TEXT, cancelled_at TEXT);

CREATE TABLE workflow_subject (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_instance_id INTEGER NOT NULL,
  subject_type TEXT NOT NULL, subject_id TEXT NOT NULL, source_system TEXT NOT NULL,
  relationship TEXT NOT NULL DEFAULT 'PRIMARY');

CREATE TABLE step_instance (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_instance_id INTEGER NOT NULL,
  step_definition_id INTEGER NOT NULL, iteration_number INTEGER NOT NULL DEFAULT 1,
  state TEXT NOT NULL, execution_status TEXT NOT NULL DEFAULT 'NOT_READY', result_json TEXT,
  activated_at TEXT, started_at TEXT, completed_at TEXT,
  UNIQUE(workflow_instance_id, step_definition_id));

CREATE TABLE workflow_fact_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_instance_id INTEGER NOT NULL,
  fact_key TEXT NOT NULL, previous_value_json TEXT, new_value_json TEXT,
  source_type TEXT NOT NULL, source_reference TEXT, actor TEXT NOT NULL,
  revision INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00.000Z');

CREATE TABLE signal_receipt (
  id INTEGER PRIMARY KEY AUTOINCREMENT, command_id TEXT NOT NULL UNIQUE,
  workflow_instance_id INTEGER NOT NULL, signal_type TEXT NOT NULL, correlation_key TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  received_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00.000Z', consumed_at TEXT,
  consumed_step_instance_id INTEGER);

CREATE TABLE automation_job (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT NOT NULL UNIQUE,
  workflow_instance_id INTEGER NOT NULL, step_instance_id INTEGER NOT NULL,
  handler TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'QUEUED',
  attempt_count INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
  input_json TEXT NOT NULL DEFAULT '{}', result_json TEXT,
  available_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00.000Z', claimed_by TEXT,
  claimed_at TEXT, lease_expires_at TEXT, completed_at TEXT, last_error TEXT);

CREATE TABLE durable_timer (
  id INTEGER PRIMARY KEY AUTOINCREMENT, timer_key TEXT NOT NULL UNIQUE,
  workflow_instance_id INTEGER NOT NULL, step_instance_id INTEGER, timer_type TEXT NOT NULL,
  action TEXT NOT NULL, due_at TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'SCHEDULED', fired_at TEXT, cancelled_at TEXT);

CREATE TABLE event_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
  aggregate_type TEXT NOT NULL, aggregate_id TEXT NOT NULL, sequence_number INTEGER NOT NULL,
  workflow_instance_id INTEGER, step_instance_id INTEGER,
  event_type TEXT NOT NULL, actor TEXT NOT NULL, actor_org_id TEXT, actor_role TEXT,
  correlation_id TEXT, command_id TEXT, previous_state TEXT, new_state TEXT,
  previous_revision INTEGER, new_revision INTEGER, changed_fields_json TEXT,
  reason_reference TEXT, source_channel TEXT, payload_json TEXT,
  created_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00.000Z',
  UNIQUE(aggregate_type, aggregate_id, sequence_number));

CREATE TABLE workflow_command (
  command_id TEXT PRIMARY KEY, workflow_instance_id INTEGER NOT NULL,
  action TEXT NOT NULL, result_json TEXT NOT NULL,
  processed_at TEXT NOT NULL DEFAULT '2026-01-01T00:00:00.000Z');

-- A request, an assessment of it, and a grandchild that the two-level model
-- cannot represent and must flatten onto the same request.
INSERT INTO workflow_instance
  (id,workflow_version_id,title,business_type,business_key,lifecycle_state,
   execution_status,revision,created_by,parent_workflow_instance_id,
   root_workflow_instance_id,parent_step_instance_id,relationship_type,required_flag)
  VALUES
  (1,7,'The request','ISR_REQUEST','REQ-1','ACTIVE','RUNNING',4,'alice',NULL,1,NULL,NULL,1),
  (2,7,'An assessment','ISR_ASSESSMENT','ASMT-1','ACTIVE','RUNNING',2,'bob',1,1,11,'EXTERNAL',1),
  (3,7,'A grandchild','ISR_ASSESSMENT','ASMT-2','ACTIVE','COMPLETED',1,'carol',2,1,22,'INTERNAL',0);

INSERT INTO step_instance (id,workflow_instance_id,step_definition_id,state,execution_status)
  VALUES (11,1,100,'WAITING','WAITING'), (22,2,200,'READY','READY'), (33,3,300,'COMPLETED','COMPLETED');

INSERT INTO workflow_fact_history
  (workflow_instance_id,fact_key,new_value_json,source_type,actor,revision)
  VALUES (1,'tier','"GOLD"','COMMAND','alice',1), (2,'scope','"EXTERNAL"','SIGNAL','bob',1);

INSERT INTO signal_receipt (command_id,workflow_instance_id,signal_type)
  VALUES ('cmd-signal',2,'EXTERNAL_VALIDATION_COMPLETED');

INSERT INTO automation_job (job_key,workflow_instance_id,step_instance_id,handler)
  VALUES ('1:11:1',1,11,'noop');

INSERT INTO durable_timer (timer_key,workflow_instance_id,step_instance_id,timer_type,action,due_at)
  VALUES ('2:22:1:SLA_BREACH',2,22,'RELATIVE','SLA_BREACH','2030-01-01T00:00:00.000Z');

INSERT INTO event_log
  (event_id,aggregate_type,aggregate_id,sequence_number,workflow_instance_id,event_type,actor)
  VALUES ('e1','WORKFLOW','1',1,1,'WORKFLOW_STARTED','alice'),
         ('e2','WORKFLOW','1',2,1,'STEP_ACTIVATED','engine'),
         ('e3','WORKFLOW','2',1,2,'WORKFLOW_STARTED','bob'),
         ('e4','ISRP_EVIDENCE','EV-9',1,NULL,'EVIDENCE_ATTACHED','carol');

INSERT INTO workflow_command (command_id,workflow_instance_id,action,result_json)
  VALUES ('cmd-a',1,'start_workflow','{"workflow_instance_id":1,"action":"start_workflow","revision":1}'),
         ('cmd-b',2,'complete','{"workflow_instance_id":2,"action":"complete","revision":1}');
"""


class OwnerAggregateMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "v6.db"
        seed = sqlite3.connect(self.path)
        seed.executescript(VERSION_SIX)
        seed.commit()
        seed.close()

        SQLiteWorkflowRepository(self.path).initialize()
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def rows(self, sql, *args):
        return [dict(row) for row in self.connection.execute(sql, args)]

    def test_the_chain_reaches_the_current_version(self):
        self.assertEqual(
            self.rows("SELECT value FROM schema_metadata WHERE key='schema_version'")[0]["value"],
            str(SCHEMA_VERSION))

    def test_a_run_without_a_parent_becomes_a_request(self):
        requests = self.rows("SELECT id,title,lifecycle_status,revision FROM isrp_request")
        self.assertEqual([item["id"] for item in requests], [1])
        self.assertEqual(requests[0]["title"], "The request")
        self.assertEqual(requests[0]["lifecycle_status"], "ACTIVE")
        self.assertEqual(requests[0]["revision"], 4)

    def test_a_run_with_a_parent_becomes_an_assessment_of_its_root_request(self):
        assessments = self.rows(
            """SELECT id,request_id,assessment_type,parent_step_instance_id,required_flag
               FROM isrp_assessment ORDER BY id""")
        self.assertEqual([item["id"] for item in assessments], [2, 3])
        # Both, including the grandchild, hang off the one request. Flattening
        # is the documented loss: there is no third level to put it on.
        self.assertEqual({item["request_id"] for item in assessments}, {1})
        self.assertEqual(assessments[0]["assessment_type"], "EXTERNAL")
        self.assertEqual(assessments[0]["parent_step_instance_id"], 11)
        self.assertEqual(assessments[1]["required_flag"], 0)
        # The flattened grandchild hung off a node on assessment 2, which the
        # request can never satisfy, so the pointer is dropped rather than left
        # to wedge a run.
        self.assertIsNone(assessments[1]["parent_step_instance_id"])

    def test_ids_do_not_collide_across_the_two_tables(self):
        ids = ([item["id"] for item in self.rows("SELECT id FROM isrp_request")]
               + [item["id"] for item in self.rows("SELECT id FROM isrp_assessment")])
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_runtime_row_carries_the_owner_it_had_before(self):
        expected = {1: "ISRP_REQUEST", 2: "ISRP_ASSESSMENT", 3: "ISRP_ASSESSMENT"}
        for table, key in (("step_instance", "step_definition_id"),
                           ("workflow_fact_history", "fact_key"),
                           ("signal_receipt", "signal_type"),
                           ("automation_job", "job_key"),
                           ("durable_timer", "timer_key"),
                           ("workflow_command", "action")):
            with self.subTest(table=table):
                for row in self.rows(f"SELECT owner_type,owner_id,{key} FROM {table}"):
                    self.assertEqual(row["owner_type"], expected[row["owner_id"]])

    def test_step_instances_keep_their_ids_so_references_still_resolve(self):
        steps = self.rows("SELECT id,owner_type,owner_id FROM step_instance ORDER BY id")
        self.assertEqual([item["id"] for item in steps], [11, 22, 33])
        self.assertEqual(steps[1]["owner_type"], "ISRP_ASSESSMENT")
        self.assertEqual(
            self.rows("SELECT step_instance_id FROM durable_timer")[0]["step_instance_id"], 22)

    def test_the_workflow_aggregate_type_is_gone_from_the_log(self):
        events = self.rows(
            "SELECT event_id,aggregate_type,aggregate_id,sequence_number FROM event_log ORDER BY id")
        self.assertEqual(
            [(item["aggregate_type"], item["aggregate_id"], item["sequence_number"])
             for item in events],
            [("ISRP_REQUEST", "1", 1), ("ISRP_REQUEST", "1", 2),
             ("ISRP_ASSESSMENT", "2", 1), ("ISRP_EVIDENCE", "EV-9", 1)])
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(event_log)")}
        self.assertNotIn("workflow_instance_id", columns)

    def test_the_folded_tables_are_dropped(self):
        tables = {row["name"] for row in self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("workflow_instance", tables)
        self.assertNotIn("workflow_subject", tables)
        self.assertNotIn("step_instance_prior", tables)

    def test_no_table_is_left_referencing_a_rebuilt_table_by_its_old_name(self):
        """A rename during the rebuild must not drag other tables with it.

        SQLite rewrites every REFERENCES clause that names a table being
        renamed, and turning foreign keys off does not stop it. The first
        version of this migration therefore left seven tables pointing at
        step_instance_prior, which it then dropped.
        """
        definitions = self.rows(
            "SELECT name,sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL")
        dangling = [row["name"] for row in definitions if "_prior" in row["sql"]]
        self.assertEqual(dangling, [])
        self.assertIn(
            "REFERENCES step_instance(id)",
            next(row["sql"] for row in definitions if row["name"] == "step_attempt"))

    def test_a_second_start_changes_nothing(self):
        SQLiteWorkflowRepository(self.path).initialize()
        self.assertEqual(len(self.rows("SELECT id FROM isrp_request")), 1)
        self.assertEqual(len(self.rows("SELECT id FROM isrp_assessment")), 2)
        self.assertEqual(len(self.rows("SELECT id FROM step_instance")), 3)

    def test_the_upgraded_database_still_runs_new_work(self):
        """The real test of a migration: the next request starts normally."""
        from isrp.orchestration import WorkflowEngine

        repository = SQLiteWorkflowRepository(self.path)
        engine = WorkflowEngine(repository)
        version = engine.import_template({
            "key": "after-upgrade", "name": "After", "version": 1, "publish": True,
            "steps": [{"key": "work", "name": "W", "type": "HUMAN_TASK", "configuration": {}},
                      {"key": "end", "name": "E", "type": "END"}],
            "transitions": [{"from_step": "work", "to_step": "end"}],
        })["workflow_version_id"]
        request = engine.start_request({
            "command_id": "after-upgrade-1", "workflow_version_id": version,
            "title": "New", "actor": "alice", "variables": {}})
        self.assertEqual(request["execution_status"], "RUNNING")
        # The identifier sequence continued past the migrated rows rather than
        # restarting on top of them.
        self.assertGreater(request["id"], 1)


if __name__ == "__main__":
    unittest.main()
