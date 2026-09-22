"""Service levels, business calendars and version pinning: WRK-8, JOB-7, DEF-3."""

from __future__ import annotations

import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from isrp.orchestration import ValidationError, WorkflowEngine
from isrp.orchestration.calendars import deadline
from isrp.orchestration import SQLiteWorkflowRepository

from support import aggregate_args, owner


OFFICE = {"business_days": [0, 1, 2, 3, 4], "opens_at": "09:00", "closes_at": "17:00"}


def command(**values):
    return {"command_id": str(uuid.uuid4()), "actor": "tester", **values}


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class CalendarTests(unittest.TestCase):
    """JOB-7. Pure arithmetic, so it is tested without a database."""

    def test_elapsed_time_when_no_calendar_is_configured(self):
        self.assertEqual(
            deadline(at("2026-09-25T16:00"), 4 * 3600, None), at("2026-09-25T20:00"))

    def test_four_working_hours_from_friday_afternoon_land_on_monday(self):
        # One hour left on Friday, three more after the weekend.
        self.assertEqual(
            deadline(at("2026-09-25T16:00"), 4 * 3600, OFFICE), at("2026-09-28T12:00"))

    def test_work_starting_before_opening_waits_for_the_office(self):
        self.assertEqual(
            deadline(at("2026-09-21T06:00"), 3600, OFFICE), at("2026-09-21T10:00"))

    def test_a_holiday_is_skipped(self):
        calendar = dict(OFFICE, holidays=["2026-09-22"])
        self.assertEqual(
            deadline(at("2026-09-21T16:30"), 3600, calendar), at("2026-09-23T09:30"))

    def test_a_calendar_that_never_opens_is_rejected(self):
        with self.assertRaises(ValidationError):
            deadline(at("2026-09-21T09:00"), 60, {"opens_at": "17:00", "closes_at": "09:00"})
        with self.assertRaises(ValidationError):
            deadline(at("2026-09-21T09:00"), 60, {"business_days": []})


class ServiceLevelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = SQLiteWorkflowRepository(Path(self.temp.name) / "workflow.db")
        self.repository.initialize()
        self.engine = WorkflowEngine(self.repository)

    def tearDown(self):
        self.temp.cleanup()

    def template(self, key, configuration):
        return {
            "key": key, "name": key, "version": 1, "publish": True,
            "steps": [{"key": "review", "name": "Review", "type": "HUMAN_TASK",
                       "configuration": configuration},
                      {"key": "end", "name": "End", "type": "END"}],
            "transitions": [{"from_step": "review", "to_step": "end"}],
        }

    def start(self, template):
        version = self.engine.import_template(template)["workflow_version_id"]
        return self.engine.start_request(command(
            workflow_version_id=version, title="T",
            variables={}))

    def expire_service_levels(self):
        with self.repository.transaction():
            self.repository.db.execute(
                """UPDATE durable_timer SET due_at='2000-01-01T00:00:00.000Z'
                   WHERE action='SLA_BREACH'""")

    def events(self, workflow):
        with self.repository.transaction():
            return [row[0] for row in self.repository.db.execute(
                "SELECT event_type FROM event_log WHERE aggregate_type=? AND aggregate_id=?",
                aggregate_args(workflow))]

    # WRK-8: a node carries a due time, and breach raises a configured action.

    def test_wrk8_due_time_is_recorded_on_the_node_and_its_assignment(self):
        workflow = self.start(self.template(
            "sla-due", {"due_in_seconds": 3600, "candidates": []}))
        self.assertIn("STEP_DUE_AT_SET", self.events(workflow))

    def test_wrk8_breach_is_recorded_without_changing_the_node_by_default(self):
        workflow = self.start(self.template("sla-notify", {"due_in_seconds": 3600}))
        self.expire_service_levels()
        self.assertEqual(self.engine.process_due_timers(), 1)

        after = self.engine.get_aggregate(owner(workflow))
        self.assertIn("STEP_SLA_BREACHED", self.events(workflow))
        self.assertEqual(after["steps"][0]["execution_status"], "READY")
        self.assertEqual(after["execution_status"], "RUNNING")

    def test_wrk8_escalate_reassigns_the_work(self):
        workflow = self.start(self.template("sla-escalate", {
            "due_in_seconds": 3600, "on_breach": "ESCALATE",
            "escalate_to": "HEAD_OF_REVIEW"}))
        self.expire_service_levels()
        self.engine.process_due_timers()

        assignments = self.engine.get_aggregate(owner(workflow))["steps"][0]["assignments"]
        self.assertEqual(
            next(item["assignee"] for item in assignments if item["status"] == "OPEN"),
            "HEAD_OF_REVIEW")

    def test_wrk8_fail_on_breach_applies_the_node_failure_policy(self):
        workflow = self.start(self.template("sla-fail", {
            "due_in_seconds": 3600, "on_breach": "FAIL"}))
        self.expire_service_levels()
        self.engine.process_due_timers()

        after = self.engine.get_aggregate(owner(workflow))
        self.assertEqual(after["steps"][0]["execution_status"], "FAILED")
        self.assertEqual(after["execution_status"], "FAILED")

    def test_wrk8_breach_is_not_raised_for_a_node_already_finished(self):
        workflow = self.start(self.template("sla-done", {
            "due_in_seconds": 3600, "on_breach": "FAIL"}))
        step = workflow["steps"][0]
        workflow = self.engine.apply_action(step["id"], command(
            action="start", expected_revision=workflow["revision"], payload={}))
        self.engine.apply_action(step["id"], command(
            action="complete", expected_revision=workflow["revision"], payload={}))

        self.expire_service_levels()
        self.engine.process_due_timers()

        after = self.engine.get_aggregate(owner(workflow))
        self.assertEqual(after["execution_status"], "COMPLETED")
        self.assertNotIn("STEP_SLA_BREACHED", self.events(workflow))

    def test_wrk8_invalid_service_level_configuration_is_rejected(self):
        for configuration in (
            {"due_in_seconds": 0},
            {"due_in_seconds": -5},
            {"due_in_seconds": 60, "on_breach": "PANIC"},
            {"due_in_seconds": 60, "on_breach": "ESCALATE"},
            {"due_in_seconds": 60, "calendar": {"opens_at": "nonsense"}},
        ):
            with self.subTest(configuration=configuration):
                with self.assertRaises(ValidationError):
                    self.engine.import_template(
                        self.template(f"bad-{uuid.uuid4().hex[:8]}", configuration))

    # DEF-3: a running instance stays pinned to the version it started on.

    def test_def3_running_instance_is_unaffected_by_a_later_version(self):
        first = {
            "key": "pinned", "name": "Pinned", "version": 1, "publish": True,
            "steps": [{"key": "review", "name": "Review", "type": "HUMAN_TASK",
                       "configuration": {}},
                      {"key": "end", "name": "End", "type": "END"}],
            "transitions": [{"from_step": "review", "to_step": "end"}],
        }
        workflow = self.start(first)

        second = dict(first, version=2)
        second["steps"] = [
            {"key": "review", "name": "Review", "type": "HUMAN_TASK", "configuration": {}},
            {"key": "second_approval", "name": "Second approval", "type": "HUMAN_TASK",
             "configuration": {}},
            {"key": "end", "name": "End", "type": "END"},
        ]
        second["transitions"] = [
            {"from_step": "review", "to_step": "second_approval"},
            {"from_step": "second_approval", "to_step": "end"},
        ]
        self.engine.import_template(second)

        running = self.engine.get_aggregate(owner(workflow))
        self.assertEqual({item["step_key"] for item in running["steps"]},
                         {"review", "end"})

        running = self.engine.apply_action(running["steps"][0]["id"], command(
            action="start", expected_revision=running["revision"], payload={}))
        running = self.engine.apply_action(running["steps"][0]["id"], command(
            action="complete", expected_revision=running["revision"], payload={}))
        self.assertEqual(running["execution_status"], "COMPLETED",
                         "the instance must finish on the graph it started with")


if __name__ == "__main__":
    unittest.main()
