from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .errors import ConflictError, NotFoundError, ValidationError
from .ports import WorkflowRepository
from .rules import evaluate
from .validation import validate_template


SYSTEM_TYPES = {"AUTOMATED_TASK", "FORK", "JOIN", "MILESTONE", "END"}
SATISFIED_STATES = {"COMPLETED", "SKIPPED"}
TERMINAL_STATES = {"COMPLETED", "SKIPPED", "FAILED", "CANCELLED"}

STEP_TRANSITIONS = {
    "READY": {"assign": "ASSIGNED", "start": "IN_PROGRESS", "skip": "SKIPPED", "cancel": "CANCELLED"},
    "ASSIGNED": {"start": "IN_PROGRESS", "skip": "SKIPPED", "cancel": "CANCELLED"},
    "IN_PROGRESS": {
        "wait": "WAITING", "request_clarification": "CLARIFICATION_REQUIRED",
        "complete": "COMPLETED", "fail": "FAILED", "cancel": "CANCELLED",
    },
    "WAITING": {"resume": "IN_PROGRESS", "cancel": "CANCELLED"},
    "CLARIFICATION_REQUIRED": {"respond": "RESPONSE_RECEIVED", "cancel": "CANCELLED"},
    "RESPONSE_RECEIVED": {"resume": "IN_PROGRESS", "complete": "COMPLETED", "cancel": "CANCELLED"},
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowEngine:
    def __init__(self, repository: WorkflowRepository):
        self.repository = repository

    def import_template(self, template: dict[str, Any]) -> dict[str, int]:
        validate_template(template)
        with self.repository.transaction():
            return self.repository.import_template(template)

    def list_templates(self) -> list[dict[str, Any]]:
        return self.repository.list_templates()

    def get_template(self, version_id: int) -> dict[str, Any]:
        result = self.repository.get_template(version_id)
        if not result:
            raise NotFoundError("Template version not found")
        return result

    def list_workflows(self) -> list[dict[str, Any]]:
        return self.repository.list_workflows()

    def get_workflow(self, workflow_id: int) -> dict[str, Any]:
        result = self.repository.get_workflow(workflow_id)
        if not result:
            raise NotFoundError("Workflow instance not found")
        return result

    def start_workflow(self, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            previous = self.repository.command_result(command["command_id"])
            if previous:
                return previous
            version = self.repository.get_published_version(command["workflow_version_id"])
            if not version:
                raise ValidationError("Published workflow version not found")
            workflow_id = self.repository.create_workflow(command)
            self.repository.add_subjects(workflow_id, command.get("subjects", []))
            self.repository.create_step_instances(workflow_id, version["id"])
            self.repository.append_event(workflow_id, "WORKFLOW_STARTED", command.get("actor", "system"), payload={
                "business_type": command.get("business_type"),
                "business_key": command.get("business_key"),
                "correlation_id": command.get("correlation_id"),
            })
            self._drive(workflow_id)
            self.repository.update_workflow(workflow_id, {"revision": 1})
            result = self.get_workflow(workflow_id)
            self.repository.record_command(command["command_id"], workflow_id, "start_workflow", result)
            return result

    def apply_action(self, step_id: int, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            previous_result = self.repository.command_result(command["command_id"])
            if previous_result:
                return previous_result
            step = self.repository.get_step(step_id)
            if not step:
                raise NotFoundError("Step instance not found")
            workflow = self.repository.get_instance_row(step["workflow_instance_id"])
            expected = command.get("expected_revision")
            if expected is not None and expected != workflow["revision"]:
                raise ConflictError(f"Workflow revision is {workflow['revision']}; expected {expected}")
            current = step["state"]
            action = command["action"]
            target = STEP_TRANSITIONS.get(current, {}).get(action)
            if not target:
                raise ConflictError(f"Action '{action}' is not allowed from state '{current}'")
            values: dict[str, Any] = {"state": target}
            if action == "start":
                values["started_at"] = now()
            if target in TERMINAL_STATES:
                values["completed_at"] = now()
                values["result"] = command.get("payload", {})
            self.repository.update_step(step_id, values)
            if action == "assign" and command.get("assignee"):
                self.repository.replace_assignments(step_id, command.get("assignee_type", "USER"), command["assignee"])
            self.repository.append_event(
                workflow["id"], f"STEP_{action.upper()}", command.get("actor", "system"),
                step_id, current, target, command.get("payload", {}),
            )
            self._drive(workflow["id"])
            self.repository.update_workflow(workflow["id"], {"revision": workflow["revision"] + 1})
            result = self.get_workflow(workflow["id"])
            self.repository.record_command(command["command_id"], workflow["id"], action, result)
            return result

    def _can_activate(self, workflow: dict[str, Any], step: dict[str, Any]) -> bool:
        incoming = self.repository.incoming(workflow["id"], step["step_definition_id"])
        if not incoming:
            return True
        context = workflow["variables"]
        applicable = [row for row in incoming if evaluate(row.get("condition"), context)]
        if not applicable:
            return False
        if step["step_type"] == "JOIN" and step.get("join_rule") == "ANY":
            return any(row["state"] in SATISFIED_STATES for row in applicable)
        return all(row["state"] in SATISFIED_STATES for row in applicable)

    def _drive(self, workflow_id: int) -> None:
        for _ in range(100):
            changed = False
            workflow = self.repository.get_instance_row(workflow_id)
            if not workflow or workflow["status"] != "ACTIVE":
                return
            for step in self.repository.list_not_ready_steps(workflow_id):
                if not self._can_activate(workflow, step):
                    continue
                self.repository.update_step(step["id"], {"state": "READY", "activated_at": now()})
                self.repository.append_event(workflow_id, "STEP_ACTIVATED", "engine", step["id"], "NOT_READY", "READY")
                if step.get("stage"):
                    self.repository.update_workflow(workflow_id, {"current_stage": step["stage"]})
                if step.get("assignment_role"):
                    self.repository.create_assignment(step["id"], "ROLE", step["assignment_role"])
                changed = True
            for step in self.repository.list_ready_steps(workflow_id):
                if step["step_type"] not in SYSTEM_TYPES:
                    continue
                stamp = now()
                self.repository.update_step(step["id"], {"state": "COMPLETED", "started_at": stamp, "completed_at": stamp})
                self.repository.append_event(workflow_id, "SYSTEM_STEP_COMPLETED", "engine", step["id"], "READY", "COMPLETED")
                if step["step_type"] == "END":
                    self.repository.update_workflow(workflow_id, {"status": "COMPLETED", "current_stage": "End", "completed_at": stamp})
                    self.repository.append_event(workflow_id, "WORKFLOW_COMPLETED", "engine", step["id"])
                changed = True
            if not changed:
                return
        raise RuntimeError("Workflow did not reach a stable state")

