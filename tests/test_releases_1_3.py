from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from isrp.orchestration import ConflictError, WorkflowEngine
from isrp.orchestration import SQLiteWorkflowRepository

from support import owner


def command(**values):
    return {"command_id": str(uuid.uuid4()), "actor": "tester", **values}


def linear_template(key: str, first_type: str = "HUMAN_TASK", configuration=None):
    return {
        "key": key, "name": key, "version": 1, "publish": True,
        "steps": [
            {"key": "work", "name": "Work", "type": first_type,
             "configuration": configuration or {}},
            {"key": "end", "name": "End", "type": "END"},
        ],
        "transitions": [{"from_step": "work", "to_step": "end"}],
    }


class ReleaseOneToThreeTests(unittest.TestCase):
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
            "variables": {},
        }
        data.update(values)
        return self.engine.start_request(command(**data))

    def act(self, workflow, key, action, **values):
        step = next(item for item in workflow["steps"] if item["step_key"] == key)
        return self.engine.apply_action(
            step["id"], command(action=action, expected_revision=workflow["revision"], payload={}, **values)
        )

    def test_configurable_lifecycle_and_step_fsm(self):
        step_fsm = {
            "key": "test.review-step", "name": "Review", "version": 1,
            "initial_state": "NOT_READY",
            "states": ["NOT_READY", "READY", "WORKING", {"key": "DONE", "terminal": True}],
            "transitions": [
                {"action": "activate", "from": "NOT_READY", "to": "READY"},
                {"action": "claim", "from": "READY", "to": "WORKING"},
                {"action": "finish", "from": "WORKING", "to": "DONE"},
            ],
        }
        lifecycle = {
            "key": "test.review-lifecycle", "name": "Review lifecycle", "version": 1,
            "initial_state": "DRAFT",
            "states": ["DRAFT", "IN_REVIEW", {"key": "CLOSED", "terminal": True}],
            "transitions": [
                {"action": "submit", "from": "DRAFT", "to": "IN_REVIEW"},
                {"action": "complete", "from": "IN_REVIEW", "to": "CLOSED"},
            ],
        }
        template = linear_template("configurable-fsm")
        template["lifecycle_fsm"] = lifecycle
        template["steps"][0]["fsm"] = step_fsm
        workflow = self.start(self.import_template(template))
        self.assertEqual(workflow["lifecycle_status"], "DRAFT")
        workflow = self.engine.apply_lifecycle_action(owner(workflow), command(action="submit", expected_revision=workflow["revision"])
        )
        workflow = self.act(workflow, "work", "claim")
        workflow = self.act(workflow, "work", "finish")
        self.assertEqual(workflow["lifecycle_status"], "CLOSED")
        self.assertEqual(workflow["execution_status"], "COMPLETED")

    def test_a_request_waits_for_its_assessment(self):
        assessment_version = self.import_template(linear_template("external-assessment"))
        definition = {
            "key": "request-orchestration", "name": "Request", "version": 1, "publish": True,
            "steps": [
                {"key": "assessments", "name": "Assessments", "type": "ASSESSMENT",
                 "configuration": {
                     "assessment_workflow_version_id": assessment_version,
                     "assessment_type": "EXTERNAL",
                     "title": "External assessment"}},
                {"key": "end", "name": "End", "type": "END"},
            ],
            "transitions": [{"from_step": "assessments", "to_step": "end"}],
        }
        request = self.start(self.import_template(definition), title="REQ-1")
        sub = next(item for item in request["steps"] if item["step_key"] == "assessments")
        self.assertEqual(sub["execution_status"], "WAITING")
        self.assertEqual(len(request["assessments"]), 1)
        self.assertEqual(request["assessments"][0]["assessment_type"], "EXTERNAL")

        assessment = self.engine.get_assessment(request["assessments"][0]["id"])
        assessment = self.act(assessment, "work", "start")
        self.act(assessment, "work", "complete")

        request = self.engine.get_aggregate(owner(request))
        self.assertEqual(request["execution_status"], "COMPLETED")

    def test_wait_signal_and_early_signal(self):
        template = linear_template(
            "wait-signal", "WAIT_SIGNAL",
            {"signal_type": "EXTERNAL_VALIDATION_COMPLETED", "correlation_key": "FINDING-1"},
        )
        workflow = self.start(self.import_template(template))
        self.assertEqual(workflow["steps"][0]["execution_status"], "WAITING")
        workflow = self.engine.receive_signal(owner(workflow), command(
            signal_type="EXTERNAL_VALIDATION_COMPLETED", correlation_key="FINDING-1",
            payload={"result_reference": "isrp://validation/1"},
        ))
        self.assertEqual(workflow["execution_status"], "COMPLETED")

    def test_facts_drive_conditional_branch(self):
        template = {
            "key": "facts", "name": "Facts", "version": 1, "publish": True,
            "steps": [
                {"key": "select", "name": "Select", "type": "HUMAN_TASK"},
                {"key": "fork", "name": "Fork", "type": "FORK"},
                {"key": "sme", "name": "SME", "type": "HUMAN_TASK"},
                {"key": "join", "name": "Join", "type": "JOIN", "join_rule": "ALL"},
                {"key": "end", "name": "End", "type": "END"},
            ],
            "transitions": [
                {"from_step": "select", "to_step": "fork"},
                {"from_step": "fork", "to_step": "sme",
                 "condition": {"field": "sme_required", "operator": "truthy"}},
                {"from_step": "sme", "to_step": "join",
                 "condition": {"field": "sme_required", "operator": "truthy"}},
                {"from_step": "fork", "to_step": "join",
                 "condition": {"field": "sme_required", "operator": "ne", "value": True}},
                {"from_step": "join", "to_step": "end"},
            ],
        }
        workflow = self.start(self.import_template(template))
        workflow = self.engine.update_facts(owner(workflow), command(
            facts={"sme_required": True}, expected_revision=workflow["revision"],
            source_type="ISRP", source_reference="REQ-1",
        ))
        workflow = self.act(workflow, "select", "start")
        workflow = self.act(workflow, "select", "complete")
        sme = next(item for item in workflow["steps"] if item["step_key"] == "sme")
        self.assertEqual(sme["execution_status"], "READY")

    def test_candidates_and_organization_assignment(self):
        template = linear_template("organization-assignment")
        template["steps"][0]["configuration"] = {
            "candidates": [
                {"type": "GROUP", "value": "IDENTITY_SME", "organization_id": "ORG-A"},
                {"type": "GROUP", "value": "IDENTITY_SME", "organization_id": "ORG-B"},
            ]
        }
        workflow = self.start(self.import_template(template))
        work = workflow["steps"][0]
        self.assertEqual(len(work["candidates"]), 2)
        workflow = self.act(
            workflow, "work", "assign", assignee="alice", organization_id="ORG-B",
            reason="Selected reviewer",
        )
        assignment = next(item for item in workflow["steps"][0]["assignments"] if item["status"] == "OPEN")
        self.assertEqual(assignment["organization_id"], "ORG-B")

    def test_claim_enforces_candidate_organization(self):
        template = linear_template("candidate-claim")
        template["steps"][0]["configuration"] = {
            "candidates": [
                {"type": "GROUP", "value": "IDENTITY_SME", "organization_id": "ORG-B"}
            ]
        }
        workflow = self.start(self.import_template(template))
        work = workflow["steps"][0]
        with self.assertRaises(ConflictError):
            self.engine.apply_action(work["id"], command(
                action="claim", expected_revision=workflow["revision"], payload={},
                actor={"actor_id": "alice", "organization_id": "ORG-A",
                       "groups": ["IDENTITY_SME"], "permissions": []},
            ))
        workflow = self.engine.apply_action(work["id"], command(
            action="claim", expected_revision=workflow["revision"], payload={},
            actor={"actor_id": "bob", "organization_id": "ORG-B",
                   "groups": ["IDENTITY_SME"], "permissions": []},
        ))
        open_assignment = next(
            item for item in workflow["steps"][0]["assignments"] if item["status"] == "OPEN"
        )
        self.assertEqual(open_assignment["assignee"], "bob")

    def test_automation_job_claim_and_complete(self):
        version = self.import_template(linear_template(
            "automation", "AUTOMATED_TASK", {"handler": "deterministic.check", "max_attempts": 2}
        ))
        workflow = self.start(version)
        jobs = self.engine.claim_automation_jobs("worker-1")
        self.assertEqual(len(jobs), 1)
        workflow = self.engine.complete_automation_job(
            jobs[0]["id"], command(success=True, result={"result_reference": "isrp://checks/1"})
        )
        self.assertEqual(workflow["execution_status"], "COMPLETED")

    def test_timer_and_lifecycle_operations(self):
        version = self.import_template(linear_template("timer", "TIMER", {"delay_seconds": 0}))
        workflow = self.start(version)
        workflow = self.engine.apply_lifecycle_action(owner(workflow), command(action="suspend", expected_revision=workflow["revision"])
        )
        self.assertEqual(workflow["execution_status"], "SUSPENDED")
        workflow = self.engine.apply_lifecycle_action(owner(workflow), command(action="resume", expected_revision=workflow["revision"])
        )
        self.assertEqual(workflow["execution_status"], "RUNNING")
        self.assertEqual(self.engine.process_due_timers(), 1)
        self.assertEqual(self.engine.get_aggregate(owner(workflow))["execution_status"], "COMPLETED")

    def test_duplicate_signal_is_idempotent(self):
        version = self.import_template(linear_template(
            "signal-idempotency", "WAIT_SIGNAL", {"signal_type": "READY"}
        ))
        workflow = self.start(version)
        signal = command(signal_type="READY", payload={})
        first = self.engine.receive_signal(owner(workflow), signal)
        second = self.engine.receive_signal(owner(workflow), signal)
        self.assertEqual(first["revision"], second["revision"])

    def test_external_inbox_event_is_deduplicated(self):
        version = self.import_template(linear_template(
            "external-inbox", "WAIT_SIGNAL", {"signal_type": "ISSUE_RESOLVED"}
        ))
        workflow = self.start(version)
        event = {
            "connector_name": "issue-manager",
            "provider_event_id": "event-123",
            "event_type": "ISSUE_RESOLVED",
            "payload": {"external_issue_id": "ISS-9"},
        }
        first = self.engine.ingest_external_event(owner(workflow), event)
        second = self.engine.ingest_external_event(owner(workflow), event)
        self.assertEqual(first["revision"], second["revision"])
        with self.repository.transaction():
            count = self.repository.db.execute("SELECT COUNT(*) FROM inbox_event").fetchone()[0]
        self.assertEqual(count, 1)

    def test_suspend_rejects_step_actions(self):
        workflow = self.start(self.import_template(linear_template("suspend")))
        workflow = self.engine.apply_lifecycle_action(owner(workflow), command(action="suspend", expected_revision=workflow["revision"])
        )
        with self.assertRaises(ConflictError):
            self.act(workflow, "work", "start")

    def test_authorized_join_override(self):
        template = {
            "key": "join-override", "name": "Join override", "version": 1, "publish": True,
            "steps": [
                {"key": "fork", "name": "Fork", "type": "FORK"},
                {"key": "a", "name": "A", "type": "HUMAN_TASK"},
                {"key": "b", "name": "B", "type": "HUMAN_TASK"},
                {"key": "join", "name": "Join", "type": "JOIN", "join_rule": "ALL"},
                {"key": "end", "name": "End", "type": "END"},
            ],
            "transitions": [
                {"from_step": "fork", "to_step": "a"}, {"from_step": "fork", "to_step": "b"},
                {"from_step": "a", "to_step": "join"}, {"from_step": "b", "to_step": "join"},
                {"from_step": "join", "to_step": "end"},
            ],
        }
        workflow = self.start(self.import_template(template))
        workflow = self.act(workflow, "a", "start")
        workflow = self.act(workflow, "a", "complete")
        join = next(item for item in workflow["steps"] if item["step_key"] == "join")
        workflow = self.engine.apply_action(join["id"], command(
            action="override_join", expected_revision=workflow["revision"],
            reason="Approved emergency override", payload={},
            actor={"actor_id": "admin", "permissions": ["workflow.override"]},
        ))
        self.assertEqual(workflow["execution_status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
