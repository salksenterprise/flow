"""Execution semantics: docs/requirements.md sections B, C and E.

Covers the rows closed after the P0 work: declared state categories, dead-path
skipping, node and child failure policies, join rules, and the extended guard
language.
"""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from isrp.orchestration import ValidationError, WorkflowEngine
from isrp.orchestration import SQLiteWorkflowRepository

from support import owner


def command(**values):
    return {"command_id": str(uuid.uuid4()), "actor": "tester", **values}


class SemanticsTests(unittest.TestCase):
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

    def execution(self, workflow):
        return {item["step_key"]: item["execution_status"] for item in workflow["steps"]}

    @staticmethod
    def fan_out(join_rule, condition, on_failure=None):
        """fork -> {a, b} -> join -> end, with a's edge into the join conditional."""
        work = {"key": "a", "name": "A", "type": "HUMAN_TASK", "configuration": {}}
        if on_failure:
            work["configuration"] = {"on_failure": on_failure}
        return {
            "key": f"fan-{uuid.uuid4().hex[:8]}", "name": "Fan", "version": 1, "publish": True,
            "steps": [
                {"key": "fork", "name": "Fork", "type": "FORK"},
                work,
                {"key": "b", "name": "B", "type": "HUMAN_TASK", "configuration": {}},
                {"key": "join", "name": "Join", "type": "JOIN", "join_rule": join_rule},
                {"key": "end", "name": "End", "type": "END"},
            ],
            "transitions": [
                {"from_step": "fork", "to_step": "a"},
                {"from_step": "fork", "to_step": "b"},
                {"from_step": "a", "to_step": "join", "condition": condition},
                {"from_step": "b", "to_step": "join"},
                {"from_step": "join", "to_step": "end"},
            ],
        }

    # EXE-13: a state declares its execution category.

    def test_exe13_declared_category_overrides_the_state_name(self):
        fsm = {
            "key": "test.parked", "name": "Parked", "version": 1,
            "initial_state": "NOT_READY",
            "states": ["NOT_READY", "READY",
                       {"key": "PARKED", "category": "WAITING"},
                       {"key": "DONE", "terminal": True, "category": "COMPLETED"}],
            "transitions": [
                {"action": "activate", "from": "NOT_READY", "to": "READY"},
                {"action": "park", "from": "READY", "to": "PARKED"},
                {"action": "finish", "from": "PARKED", "to": "DONE"},
            ],
        }
        template = {
            "key": "categories", "name": "Categories", "version": 1, "publish": True,
            "steps": [{"key": "work", "name": "W", "type": "HUMAN_TASK", "fsm": fsm,
                       "configuration": {}},
                      {"key": "end", "name": "E", "type": "END"}],
            "transitions": [{"from_step": "work", "to_step": "end"}],
        }
        workflow = self.act(self.start(template), "work", "park")
        # PARKED is not a name the engine knows; only the declaration says WAITING.
        self.assertEqual(self.execution(workflow)["work"], "WAITING")
        workflow = self.act(workflow, "work", "finish")
        self.assertEqual(self.execution(workflow)["work"], "COMPLETED")
        self.assertEqual(workflow["execution_status"], "COMPLETED")

    def test_exe13_unknown_category_is_rejected_at_publication(self):
        with self.assertRaises(ValidationError):
            self.engine.import_template({
                "key": "bad-category", "name": "Bad", "version": 1, "publish": True,
                "steps": [{"key": "work", "name": "W", "type": "HUMAN_TASK",
                           "fsm": {"key": "test.bad", "name": "Bad", "version": 1,
                                   "initial_state": "READY",
                                   "states": [{"key": "READY", "category": "NONSENSE"}],
                                   "transitions": []},
                           "configuration": {}},
                          {"key": "end", "name": "E", "type": "END"}],
                "transitions": [{"from_step": "work", "to_step": "end"}],
            })

    # EXE-9: a node whose branch cannot run is skipped, not left pending.

    def test_exe9_unreached_node_is_skipped_rather_than_pending(self):
        template = {
            "key": "dead-branch", "name": "Dead branch", "version": 1, "publish": True,
            "steps": [
                {"key": "route", "name": "Route", "type": "HUMAN_TASK", "configuration": {}},
                {"key": "express", "name": "Express", "type": "HUMAN_TASK", "configuration": {}},
                {"key": "standard", "name": "Standard", "type": "HUMAN_TASK", "configuration": {}},
                {"key": "join", "name": "Join", "type": "JOIN", "join_rule": "ANY"},
                {"key": "end", "name": "End", "type": "END"},
            ],
            "transitions": [
                {"from_step": "route", "to_step": "express",
                 "condition": {"field": "expedited", "operator": "truthy"}},
                {"from_step": "route", "to_step": "standard",
                 "condition": {"field": "expedited", "operator": "ne", "value": True}},
                {"from_step": "express", "to_step": "join"},
                {"from_step": "standard", "to_step": "join"},
                {"from_step": "join", "to_step": "end"},
            ],
        }
        workflow = self.start(template, variables={"expedited": False})
        workflow = self.act(workflow, "route", "start")
        workflow = self.act(workflow, "route", "complete")
        self.assertEqual(self.execution(workflow)["express"], "SKIPPED")
        self.assertEqual(self.execution(workflow)["standard"], "READY")

    # EXE-10: a failed node applies its declared policy.

    def test_exe10_default_policy_fails_the_workflow(self):
        workflow = self.start(self.fan_out("ALL", None))
        workflow = self.act(workflow, "a", "start")
        workflow = self.act(workflow, "a", "fail")
        self.assertEqual(workflow["execution_status"], "FAILED")
        self.assertEqual(workflow["execution_status"], "FAILED")

    def test_exe10_suspend_policy_holds_the_workflow_for_intervention(self):
        workflow = self.start(self.fan_out("ALL", None, on_failure="SUSPEND"))
        workflow = self.act(workflow, "a", "start")
        workflow = self.act(workflow, "a", "fail")
        self.assertEqual(workflow["execution_status"], "SUSPENDED")

    def test_exe10_continue_policy_lets_the_graph_proceed(self):
        workflow = self.start(self.fan_out("ALL", None, on_failure="CONTINUE"))
        workflow = self.act(workflow, "a", "start")
        workflow = self.act(workflow, "a", "fail")
        self.assertEqual(workflow["execution_status"], "RUNNING")
        workflow = self.act(workflow, "b", "start")
        workflow = self.act(workflow, "b", "complete")
        self.assertEqual(workflow["execution_status"], "COMPLETED")

    def test_exe10_unknown_failure_policy_is_rejected_at_publication(self):
        with self.assertRaises(ValidationError):
            self.engine.import_template(self.fan_out("ALL", None, on_failure="EXPLODE"))

    # EXE-7: ALL_REQUIRED is distinct from ALL.

    def test_exe7_all_ignores_a_predecessor_whose_edge_does_not_apply(self):
        never = {"field": "flag", "operator": "truthy"}
        workflow = self.start(self.fan_out("ALL", never))
        workflow = self.act(workflow, "b", "start")
        workflow = self.act(workflow, "b", "complete")
        # a is still READY, but its edge never applied, so ALL is satisfied.
        self.assertEqual(self.execution(workflow)["a"], "READY")
        self.assertEqual(workflow["execution_status"], "COMPLETED")

    def test_exe7_all_required_waits_for_every_predecessor(self):
        never = {"field": "flag", "operator": "truthy"}
        workflow = self.start(self.fan_out("ALL_REQUIRED", never))
        workflow = self.act(workflow, "b", "start")
        workflow = self.act(workflow, "b", "complete")
        self.assertEqual(workflow["execution_status"], "RUNNING")
        self.assertEqual(self.execution(workflow)["join"], "NOT_READY")
        workflow = self.act(workflow, "a", "start")
        workflow = self.act(workflow, "a", "complete")
        self.assertEqual(workflow["execution_status"], "COMPLETED")

    # EXE-15: an assessment failure policy other than fail-the-request.

    def test_exe15_assessment_failure_policy_continue_lets_the_request_proceed(self):
        assessment_version = self.engine.import_template({
            "key": "afp-assessment", "name": "Assessment", "version": 1, "publish": True,
            "steps": [{"key": "work", "name": "W", "type": "HUMAN_TASK", "configuration": {}},
                      {"key": "end", "name": "E", "type": "END"}],
            "transitions": [{"from_step": "work", "to_step": "end"}],
        })["workflow_version_id"]
        request = self.start({
            "key": "afp-request", "name": "Request", "version": 1, "publish": True,
            "steps": [{"key": "sub", "name": "Sub", "type": "ASSESSMENT",
                       "configuration": {
                           "assessment_workflow_version_id": assessment_version,
                           "assessment_failure_policy": "CONTINUE"}},
                      {"key": "end", "name": "E", "type": "END"}],
            "transitions": [{"from_step": "sub", "to_step": "end"}],
        })
        # The ASSESSMENT node opened the assessment itself; nothing external
        # had to be told to start one.
        assessment = self.engine.get_assessment(request["assessments"][0]["id"])
        assessment = self.act(assessment, "work", "start")
        self.act(assessment, "work", "fail")

        self.assertEqual(
            self.engine.get_aggregate(owner(request))["execution_status"], "COMPLETED")

    def test_exe15_an_assessment_node_is_refused_inside_an_assessment(self):
        """There are two levels, so the third has nowhere to go."""
        inner = self.engine.import_template({
            "key": "nested-inner", "name": "Inner", "version": 1, "publish": True,
            "steps": [{"key": "work", "name": "W", "type": "HUMAN_TASK", "configuration": {}},
                      {"key": "end", "name": "E", "type": "END"}],
            "transitions": [{"from_step": "work", "to_step": "end"}],
        })["workflow_version_id"]
        nesting = self.engine.import_template({
            "key": "nested-middle", "name": "Middle", "version": 1, "publish": True,
            "steps": [{"key": "sub", "name": "Sub", "type": "ASSESSMENT",
                       "configuration": {"assessment_workflow_version_id": inner}},
                      {"key": "end", "name": "E", "type": "END"}],
            "transitions": [{"from_step": "sub", "to_step": "end"}],
        })["workflow_version_id"]
        with self.assertRaises(ValidationError) as raised:
            self.start({
                "key": "nested-request", "name": "Request", "version": 1, "publish": True,
                "steps": [{"key": "sub", "name": "Sub", "type": "ASSESSMENT",
                           "configuration": {"assessment_workflow_version_id": nesting}},
                          {"key": "end", "name": "E", "type": "END"}],
                "transitions": [{"from_step": "sub", "to_step": "end"}],
            })
        self.assertIn("only appear in a request", str(raised.exception))

    # EVT-4 and DEF-8: the guard language.

    def test_evt4_numeric_comparison_selects_a_branch(self):
        template = self.fan_out("ANY", {"field": "amount", "operator": "gt", "value": 1000})
        workflow = self.start(template, variables={"amount": 5000})
        workflow = self.act(workflow, "a", "start")
        workflow = self.act(workflow, "a", "complete")
        self.assertEqual(workflow["execution_status"], "COMPLETED")

    def test_evt4_numeric_comparison_is_false_for_a_non_numeric_fact(self):
        template = self.fan_out("ALL", {"field": "amount", "operator": "gt", "value": 1000})
        workflow = self.start(template, variables={"amount": "not a number"})
        workflow = self.act(workflow, "b", "start")
        workflow = self.act(workflow, "b", "complete")
        self.assertEqual(workflow["execution_status"], "COMPLETED")

    def test_evt4_dotted_path_reads_a_nested_fact(self):
        template = self.fan_out(
            "ANY", {"field": "subject.classification", "operator": "eq", "value": "RESTRICTED"})
        workflow = self.start(
            template, variables={"subject": {"classification": "RESTRICTED"}})
        workflow = self.act(workflow, "a", "start")
        workflow = self.act(workflow, "a", "complete")
        self.assertEqual(workflow["execution_status"], "COMPLETED")

    def test_def8_unknown_operator_is_rejected_at_publication(self):
        with self.assertRaises(ValidationError) as raised:
            self.engine.import_template(
                self.fan_out("ALL", {"field": "amount", "operator": "approximately", "value": 1}))
        self.assertIn("approximately", str(raised.exception))

    def test_def8_comparison_without_a_numeric_value_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.engine.import_template(
                self.fan_out("ALL", {"field": "amount", "operator": "gt", "value": "big"}))


if __name__ == "__main__":
    unittest.main()


class ExecutionModeTests(unittest.TestCase):
    """Who performs a step, and the AI modes publication refuses.

    This capability was impossible while orchestration was a domain-neutral
    engine: it had no execution-mode field, so the delivery plan's requirement
    to reject AI modes at publication could not be enforced anywhere.
    """

    # Its own fixture, so it does not re-run the suite above.
    fan_out = staticmethod(SemanticsTests.fan_out)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = SQLiteWorkflowRepository(Path(self.temp.name) / "workflow.db")
        self.repository.initialize()
        self.engine = WorkflowEngine(self.repository)

    def tearDown(self):
        self.temp.cleanup()

    def step_modes(self, version_id):
        with self.repository.transaction():
            return {row["step_key"]: row["execution_mode"] for row in
                    self.repository.db.execute(
                        "SELECT step_key,execution_mode FROM step_definition "
                        "WHERE workflow_version_id=?", (version_id,))}

    def test_mode_defaults_from_the_node_type(self):
        template = {
            "key": "modes-default", "name": "Modes", "version": 1, "publish": True,
            "steps": [
                {"key": "review", "name": "Review", "type": "HUMAN_TASK", "configuration": {}},
                {"key": "decide", "name": "Decide", "type": "DECISION", "configuration": {}},
                {"key": "scan", "name": "Scan", "type": "AUTOMATED_TASK",
                 "configuration": {"handler": "malware.scan"}},
                {"key": "end", "name": "End", "type": "END"},
            ],
            "transitions": [{"from_step": "review", "to_step": "decide"},
                            {"from_step": "decide", "to_step": "scan"},
                            {"from_step": "scan", "to_step": "end"}],
        }
        modes = self.step_modes(self.engine.import_template(template)["workflow_version_id"])
        self.assertEqual(modes["review"], "HUMAN")
        self.assertEqual(modes["decide"], "HUMAN")
        self.assertEqual(modes["scan"], "AUTOMATION")
        self.assertIsNone(modes["end"], "the engine drives an END node; nobody performs it")

    def test_an_explicit_mode_is_kept(self):
        template = self.fan_out("ALL", None)
        template["steps"][1]["execution_mode"] = "AUTOMATION"
        modes = self.step_modes(self.engine.import_template(template)["workflow_version_id"])
        self.assertEqual(modes["a"], "AUTOMATION")

    def test_ai_modes_are_refused_at_publication(self):
        for mode in ("AI_ASSISTED_HUMAN", "AI_AUTOMATED_SUPERVISED"):
            with self.subTest(mode=mode):
                template = self.fan_out("ALL", None)
                template["steps"][1]["execution_mode"] = mode
                with self.assertRaises(ValidationError) as raised:
                    self.engine.import_template(template)
                message = str(raised.exception)
                self.assertIn(mode, message)
                self.assertIn("future", message.lower())

    def test_an_unknown_mode_is_refused(self):
        template = self.fan_out("ALL", None)
        template["steps"][1]["execution_mode"] = "TELEPATHY"
        with self.assertRaises(ValidationError):
            self.engine.import_template(template)

    def test_an_engine_driven_node_cannot_declare_a_mode(self):
        template = self.fan_out("ALL", None)
        joins = next(item for item in template["steps"] if item["key"] == "join")
        joins["execution_mode"] = "HUMAN"
        with self.assertRaises(ValidationError) as raised:
            self.engine.import_template(template)
        self.assertIn("cannot declare an execution mode", str(raised.exception))
