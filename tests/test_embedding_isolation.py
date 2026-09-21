"""Namespacing and the actor type: EMB-4, EMB-5, EMB-8, NFR-5, WRK-6, WRK-7."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from workflow_core import Actor, ConflictError, WorkflowEngine
from workflow_sqlite import SQLiteWorkflowRepository
from workflow_sqlite.repository import apply_prefix

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "workflow-api"))
from app.security import actor_from_header  # noqa: E402


def command(**values):
    return {"command_id": str(uuid.uuid4()), "actor": "tester", **values}


TEMPLATE = {
    "key": "isolation", "name": "Isolation", "version": 1, "publish": True,
    "steps": [
        {"key": "review", "name": "Review", "type": "HUMAN_TASK",
         "assignment_role": "APPROVER",
         "configuration": {"candidates": [{"type": "GROUP", "value": "APPROVERS"}]}},
        {"key": "end", "name": "End", "type": "END"},
    ],
    "transitions": [{"from_step": "review", "to_step": "end"}],
}


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "shared.db"

    def tearDown(self):
        self.temp.cleanup()

    def engine_with(self, prefix=""):
        repository = SQLiteWorkflowRepository(self.path, table_prefix=prefix)
        repository.initialize()
        engine = WorkflowEngine(repository)
        version = engine.import_template(TEMPLATE)["workflow_version_id"]
        return repository, engine, version

    def start(self, engine, version, **values):
        data = {"workflow_version_id": version, "title": "T", "business_type": "APPROVAL",
                "business_key": str(uuid.uuid4()), "variables": {}, "subjects": []}
        data.update(values)
        return engine.start_workflow(command(**data))

    # EMB-5: Flow's tables can be namespaced.

    def test_emb5_prefix_rewrites_object_names_but_not_lookalike_columns(self):
        sql = "SELECT workflow_instance_id FROM workflow_instance JOIN step_instance"
        self.assertEqual(
            apply_prefix(sql, "flow_"),
            "SELECT workflow_instance_id FROM flow_workflow_instance JOIN flow_step_instance")
        self.assertEqual(apply_prefix(sql, ""), sql)

    def test_emb5_prefixed_tables_coexist_with_a_host_table_of_the_same_name(self):
        host = sqlite3.connect(str(self.path))
        host.execute("""CREATE TABLE workflow_instance (
            id INTEGER PRIMARY KEY, host_column TEXT NOT NULL)""")
        host.execute("INSERT INTO workflow_instance(id,host_column) VALUES (1,'the host owns this')")
        host.commit()

        repository, engine, version = self.engine_with(prefix="flow_")
        workflow = self.start(engine, version)
        self.assertEqual(workflow["execution_status"], "RUNNING")

        tables = {row[0] for row in host.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("flow_workflow_instance", tables)
        self.assertIn("workflow_instance", tables)
        self.assertEqual(
            host.execute("SELECT host_column FROM workflow_instance").fetchone()[0],
            "the host owns this")
        host.close()

    # EMB-8: identity is a value, not a parsed credential.

    def test_emb8_actor_reads_every_shape_a_caller_might_send(self):
        self.assertEqual(Actor.from_value("alice").actor_id, "alice")
        self.assertEqual(Actor.from_value(None).actor_type, "SYSTEM")
        typed = Actor.from_value({"actor_id": "bob", "permissions": ["workflow.repair"]})
        self.assertTrue(typed.has("workflow.repair"))
        self.assertIs(Actor.from_value(typed), typed)
        with self.assertRaises(TypeError):
            Actor.from_value(42)

    def test_emb8_typed_actor_is_accepted_by_a_command(self):
        repository, engine, version = self.engine_with()
        workflow = self.start(engine, version)
        step = workflow["steps"][0]
        with self.assertRaises(ConflictError):
            engine.apply_action(step["id"], command(
                action="claim", expected_revision=workflow["revision"], payload={},
                actor=Actor(actor_id="mallory", groups=frozenset({"OUTSIDERS"}))))
        result = engine.apply_action(step["id"], command(
            action="claim", expected_revision=workflow["revision"], payload={},
            actor=Actor(actor_id="alice", groups=frozenset({"APPROVERS"}))))
        assignments = result["steps"][0]["assignments"]
        self.assertEqual(
            next(item["assignee"] for item in assignments if item["status"] == "OPEN"),
            "alice")

    # NFR-5: authority never comes from the request body.

    def test_nfr5_absent_header_yields_an_actor_with_no_permissions(self):
        actor = actor_from_header(None)
        self.assertEqual(actor["actor_id"], "anonymous")
        self.assertEqual(actor["permissions"], [])

    def test_nfr5_permissions_come_only_from_the_trusted_header(self):
        actor = actor_from_header('{"actor_id":"ops","permissions":["workflow.repair"]}')
        self.assertEqual(actor["permissions"], ["workflow.repair"])

    def test_nfr5_malformed_or_anonymous_header_is_rejected(self):
        with self.assertRaises(ValueError):
            actor_from_header("not json")
        with self.assertRaises(ValueError):
            actor_from_header('{"permissions":["workflow.repair"]}')

    def test_nfr5_client_request_model_cannot_express_a_permission(self):
        from app.schemas import ActorContext
        self.assertNotIn("permissions", ActorContext.model_fields)
        self.assertNotIn("roles", ActorContext.model_fields)

    # WRK-6 and WRK-7: work is queryable and paginated.

    def test_wrk6_work_is_queryable_by_assignee_and_business_key(self):
        repository, engine, version = self.engine_with()
        first = self.start(engine, version, business_key="REQ-1")
        self.start(engine, version, business_key="REQ-2")
        engine.apply_action(first["steps"][0]["id"], command(
            action="assign", assignee="alice", expected_revision=first["revision"],
            payload={}))

        mine = engine.list_work(assignee="alice")
        self.assertEqual(mine["total"], 1)
        self.assertEqual(mine["items"][0]["business_key"], "REQ-1")

        by_key = engine.list_work(business_key="REQ-2")
        self.assertEqual(by_key["total"], 1)
        self.assertEqual(by_key["items"][0]["step_key"], "review")

    def test_wrk6_candidate_query_answers_what_an_actor_could_claim(self):
        repository, engine, version = self.engine_with()
        self.start(engine, version)
        eligible = engine.list_work(
            actor={"actor_id": "alice", "groups": ["APPROVERS"], "roles": []})
        self.assertEqual(eligible["total"], 1)
        outsider = engine.list_work(
            actor={"actor_id": "mallory", "groups": ["OUTSIDERS"], "roles": []})
        self.assertEqual(outsider["total"], 0)

    def test_wrk7_results_are_paginated_with_a_stable_total(self):
        repository, engine, version = self.engine_with()
        for index in range(5):
            self.start(engine, version, business_key=f"REQ-{index}")
        page = engine.list_work(limit=2, offset=0)
        self.assertEqual(page["total"], 5)
        self.assertEqual(len(page["items"]), 2)
        last = engine.list_work(limit=2, offset=4)
        self.assertEqual(len(last["items"]), 1)
        self.assertEqual(last["total"], 5)

    # NFR-8: one timestamp representation.

    def test_nfr8_stored_timestamps_share_one_format(self):
        repository, engine, version = self.engine_with()
        workflow = self.start(engine, version)
        engine.apply_action(workflow["steps"][0]["id"], command(
            action="start", expected_revision=workflow["revision"], payload={}))
        with repository.transaction():
            stamps = [row[0] for row in repository.db.execute(
                """SELECT created_at FROM workflow_event
                   UNION ALL SELECT created_at FROM workflow_instance
                   UNION ALL SELECT started_at FROM step_attempt""")]
        self.assertTrue(stamps)
        for stamp in stamps:
            self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


if __name__ == "__main__":
    unittest.main()
