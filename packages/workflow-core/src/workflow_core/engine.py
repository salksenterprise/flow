from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .errors import ConflictError, NotFoundError, ValidationError
from .ports import WorkflowRepository
from .rules import evaluate
from .validation import validate_template


SATISFIED_EXECUTION = {"COMPLETED", "SKIPPED"}
TERMINAL_EXECUTION = {"COMPLETED", "SKIPPED", "FAILED", "CANCELLED"}
IMMEDIATE_TYPES = {"FORK", "JOIN", "MILESTONE"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def actor_id(command: dict[str, Any]) -> str:
    actor = command.get("actor", "system")
    return actor.get("actor_id", "system") if isinstance(actor, dict) else actor


def actor_org(command: dict[str, Any]) -> str | None:
    actor = command.get("actor")
    return actor.get("organization_id") if isinstance(actor, dict) else None


def actor_permissions(command: dict[str, Any]) -> set[str]:
    actor = command.get("actor")
    return set(actor.get("permissions", [])) if isinstance(actor, dict) else set()


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

    def _validate_revision(self, workflow: dict[str, Any], command: dict[str, Any]) -> None:
        expected = command.get("expected_revision")
        if expected is not None and expected != workflow["revision"]:
            raise ConflictError(f"Workflow revision is {workflow['revision']}; expected {expected}")

    def start_workflow(self, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            previous = self.repository.command_result(command["command_id"])
            if previous:
                return previous
            version = self.repository.get_published_version(command["workflow_version_id"])
            if not version:
                raise ValidationError("Published workflow version not found")
            workflow_id = self.repository.create_workflow(command, version)
            self.repository.add_subjects(workflow_id, command.get("subjects", []))
            self.repository.create_step_instances(workflow_id, version["id"])
            self.repository.append_event(
                workflow_id, "WORKFLOW_STARTED", actor_id(command),
                payload={"business_type": command.get("business_type"),
                         "business_key": command.get("business_key"),
                         "correlation_id": command.get("correlation_id")},
                actor_org_id=actor_org(command),
            )
            self._drive(workflow_id)
            workflow = self.repository.get_instance_row(workflow_id)
            self.repository.update_workflow(workflow_id, {"revision": workflow["revision"] + 1})
            result = self.get_workflow(workflow_id)
            self.repository.record_command(command["command_id"], workflow_id, "start_workflow", result)
            return result

    def start_child_workflow(self, parent_id: int, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            previous = self.repository.command_result(command["command_id"])
            if previous:
                return previous
            parent = self.repository.get_instance_row(parent_id)
            if not parent:
                raise NotFoundError("Parent workflow instance not found")
            self._validate_revision(parent, command)
            step_id = command.get("parent_step_instance_id")
            if step_id:
                step = self.repository.get_step(step_id)
                if not step or step["workflow_instance_id"] != parent_id or step["step_type"] != "SUBWORKFLOW":
                    raise ValidationError("parent_step_instance_id must identify a SUBWORKFLOW node on the parent")
            child_command = dict(command)
            child_command["parent_workflow_instance_id"] = parent_id
            child_command.setdefault("root_workflow_instance_id", parent["root_workflow_instance_id"])
            child_command.setdefault("correlation_id", parent["correlation_id"])
            child = self.start_workflow(child_command)
            refreshed = self.repository.get_instance_row(parent_id)
            self.repository.update_workflow(parent_id, {"revision": refreshed["revision"] + 1})
            self.repository.append_event(
                parent_id, "CHILD_WORKFLOW_STARTED", actor_id(command), step_id,
                payload={"child_workflow_instance_id": child["id"],
                         "relationship_type": command.get("relationship_type"),
                         "relationship_key": command.get("relationship_key"),
                         "required": command.get("required", True)},
                actor_org_id=actor_org(command),
            )
            self._drive(parent_id)
            return child

    def apply_action(self, step_id: int, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            previous_result = self.repository.command_result(command["command_id"])
            if previous_result:
                return previous_result
            step = self.repository.get_step(step_id)
            if not step:
                raise NotFoundError("Step instance not found")
            workflow = self.repository.get_instance_row(step["workflow_instance_id"])
            self._validate_revision(workflow, command)
            if workflow["execution_status"] != "RUNNING":
                raise ConflictError(f"Workflow is {workflow['execution_status']}")
            if command["action"] == "override_join":
                if step["step_type"] != "JOIN":
                    raise ConflictError("Only a JOIN node can be overridden")
                if "workflow.override" not in actor_permissions(command):
                    raise ConflictError("Join override requires workflow.override permission")
                if not command.get("reason"):
                    raise ValidationError("A reason is required to override a join")
                self._complete_system_step(
                    workflow["id"], step, "JOIN_OVERRIDDEN",
                    {"reason": command["reason"], "actor": actor_id(command)},
                )
                self._drive(workflow["id"])
                current = self.repository.get_instance_row(workflow["id"])
                self.repository.update_workflow(
                    workflow["id"], {"revision": current["revision"] + 1}
                )
                result = self.get_workflow(workflow["id"])
                self.repository.record_command(
                    command["command_id"], workflow["id"], "override_join", result
                )
                return result
            if command["action"] == "claim" and isinstance(command.get("actor"), dict) and \
                    not self.repository.candidate_allowed(step_id, command["actor"]):
                raise ConflictError("Actor is not an eligible candidate for this work")
            transition = self.repository.find_fsm_transition(
                step["step_fsm_version_id"], step["state"], command["action"]
            )
            if not transition:
                raise ConflictError(
                    f"Action '{command['action']}' is not allowed from state '{step['state']}'"
                )
            if transition.get("guard") and not evaluate(transition["guard"], workflow["variables"]):
                raise ConflictError("Transition guard is not satisfied")
            permission = transition.get("required_permission")
            if permission and permission not in actor_permissions(command):
                raise ConflictError(f"Transition requires permission '{permission}'")
            if transition.get("reason_required") and not command.get("reason"):
                raise ValidationError("A reason is required for this transition")
            target = transition["to_state"]
            values: dict[str, Any] = {"state": target}
            if command["action"] in {"start", "resume"}:
                values["started_at"] = step.get("started_at") or now()
                values["execution_status"] = "ACTIVE"
                self.repository.record_attempt_start(step_id, step["iteration_number"])
            elif target in {"WAITING", "CLARIFICATION_REQUIRED"}:
                values["execution_status"] = "WAITING"
            if self.repository.state_is_terminal(step["step_fsm_version_id"], target):
                values["completed_at"] = now()
                values["result"] = command.get("payload", {})
                values["execution_status"] = (
                    "FAILED" if target == "FAILED" else
                    "CANCELLED" if target == "CANCELLED" else
                    "SKIPPED" if target == "SKIPPED" else "COMPLETED"
                )
                self.repository.record_attempt_end(
                    step_id, step["iteration_number"], target, command.get("payload", {})
                )
            elif command["action"] == "reopen":
                values.update({
                    "iteration_number": step["iteration_number"] + 1,
                    "execution_status": "READY", "completed_at": None, "result": {},
                })
            self.repository.update_step(step_id, values)
            if command["action"] in {"assign", "claim", "reassign"} and command.get("assignee"):
                self.repository.replace_assignments(
                    step_id, command.get("assignee_type", "USER"), command["assignee"],
                    command.get("organization_id"), actor_id(command), command.get("reason"),
                )
            elif command["action"] == "claim":
                self.repository.replace_assignments(
                    step_id, "USER", actor_id(command), actor_org(command), actor_id(command),
                    command.get("reason"),
                )
            self.repository.append_event(
                workflow["id"], f"STEP_{command['action'].upper()}", actor_id(command), step_id,
                step["state"], target, command.get("payload", {}), actor_org_id=actor_org(command),
            )
            self._drive(workflow["id"])
            current = self.repository.get_instance_row(workflow["id"])
            self.repository.update_workflow(workflow["id"], {"revision": current["revision"] + 1})
            result = self.get_workflow(workflow["id"])
            self.repository.record_command(command["command_id"], workflow["id"], command["action"], result)
            self._drive_parent(workflow)
            return result

    def apply_workflow_action(self, workflow_id: int, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            previous = self.repository.command_result(command["command_id"])
            if previous:
                return previous
            workflow = self.repository.get_instance_row(workflow_id)
            if not workflow:
                raise NotFoundError("Workflow instance not found")
            self._validate_revision(workflow, command)
            action = command["action"]
            previous_state = workflow["lifecycle_state"]
            values: dict[str, Any] = {}
            if action == "suspend":
                if workflow["execution_status"] != "RUNNING":
                    raise ConflictError("Only a running workflow can be suspended")
                values = {"execution_status": "SUSPENDED", "suspended_at": now()}
            elif action == "resume":
                if workflow["execution_status"] != "SUSPENDED":
                    raise ConflictError("Only a suspended workflow can be resumed")
                values = {"execution_status": "RUNNING", "suspended_at": None}
            elif action == "terminate":
                if not command.get("reason"):
                    raise ValidationError("A reason is required to terminate a workflow")
                values = {"execution_status": "CANCELLED", "cancelled_at": now()}
            else:
                version = self.repository.get_published_version(workflow["workflow_version_id"])
                transition = self.repository.find_fsm_transition(
                    version["lifecycle_fsm_version_id"], workflow["lifecycle_state"], action
                )
                if not transition:
                    raise ConflictError(
                        f"Workflow action '{action}' is not allowed from '{workflow['lifecycle_state']}'"
                    )
                if transition.get("reason_required") and not command.get("reason"):
                    raise ValidationError("A reason is required for this transition")
                permission = transition.get("required_permission")
                if permission and permission not in actor_permissions(command):
                    raise ConflictError("Workflow transition permission denied")
                values["lifecycle_state"] = transition["to_state"]
                if self.repository.state_is_terminal(
                    version["lifecycle_fsm_version_id"], transition["to_state"]
                ):
                    values["execution_status"] = (
                        "CANCELLED" if transition["to_state"] == "CANCELLED" else "COMPLETED"
                    )
                    values["status"] = (
                        "CANCELLED" if transition["to_state"] == "CANCELLED" else "COMPLETED"
                    )
                    values["completed_at"] = now()
                elif action == "reopen":
                    values.update({"status": "ACTIVE", "execution_status": "RUNNING", "completed_at": None})
            values["revision"] = workflow["revision"] + 1
            self.repository.update_workflow(workflow_id, values)
            self.repository.append_event(
                workflow_id, f"WORKFLOW_{action.upper()}", actor_id(command),
                previous=previous_state, new=values.get("lifecycle_state", previous_state),
                payload={"reason": command.get("reason")}, actor_org_id=actor_org(command),
            )
            if values.get("execution_status") == "CANCELLED":
                self._cancel_execution(workflow_id, actor_id(command), command.get("reason"))
            if values.get("execution_status") == "RUNNING":
                self._drive(workflow_id)
            result = self.get_workflow(workflow_id)
            self.repository.record_command(command["command_id"], workflow_id, action, result)
            self._drive_parent(workflow)
            return result

    def update_facts(self, workflow_id: int, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            previous = self.repository.command_result(command["command_id"])
            if previous:
                return previous
            workflow = self.repository.get_instance_row(workflow_id)
            if not workflow:
                raise NotFoundError("Workflow instance not found")
            self._validate_revision(workflow, command)
            self.repository.set_facts(
                workflow, command.get("facts", {}), actor_id(command),
                command.get("source_type", "COMMAND"), command.get("source_reference"),
            )
            self.repository.append_event(
                workflow_id, "WORKFLOW_FACTS_UPDATED", actor_id(command),
                payload={"keys": sorted(command.get("facts", {}))}, actor_org_id=actor_org(command),
            )
            self._drive(workflow_id)
            result = self.get_workflow(workflow_id)
            self.repository.record_command(command["command_id"], workflow_id, "update_facts", result)
            return result

    def receive_signal(self, workflow_id: int, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            previous = self.repository.command_result(command["command_id"])
            if previous:
                return previous
            workflow = self.repository.get_instance_row(workflow_id)
            if not workflow:
                raise NotFoundError("Workflow instance not found")
            self.repository.insert_signal(command, workflow_id)
            if command.get("apply_facts"):
                self.repository.set_facts(
                    workflow, command.get("payload", {}), actor_id(command), "SIGNAL",
                    command.get("correlation_key"),
                )
            self.repository.append_event(
                workflow_id, "SIGNAL_RECEIVED", actor_id(command),
                payload={"signal_type": command["signal_type"],
                         "correlation_key": command.get("correlation_key")},
                actor_org_id=actor_org(command),
            )
            self._drive(workflow_id)
            current = self.repository.get_instance_row(workflow_id)
            self.repository.update_workflow(workflow_id, {"revision": current["revision"] + 1})
            result = self.get_workflow(workflow_id)
            self.repository.record_command(command["command_id"], workflow_id, "signal", result)
            return result

    def ingest_external_event(self, workflow_id: int, event: dict[str, Any]) -> dict[str, Any]:
        """Persist and idempotently translate a connector event into a workflow signal."""
        with self.repository.transaction():
            existing = self.repository.inbox_event(
                event["connector_name"], event["provider_event_id"]
            )
            command_id = f"inbox:{event['connector_name']}:{event['provider_event_id']}"
            if existing and existing["status"] == "PROCESSED":
                previous = self.repository.command_result(command_id)
                return previous or self.get_workflow(workflow_id)
            inbox_id = existing["id"] if existing else self.repository.insert_inbox_event(event)
            result = self.receive_signal(workflow_id, {
                "command_id": command_id,
                "actor": f"connector:{event['connector_name']}",
                "signal_type": event["event_type"],
                "correlation_key": event.get("correlation_key"),
                "payload": event.get("payload", {}),
                "apply_facts": event.get("apply_facts", False),
            })
            self.repository.mark_inbox_processed(inbox_id)
            return result

    def claim_automation_jobs(self, worker_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self.repository.transaction():
            return self.repository.claim_jobs(worker_id, limit)

    def complete_automation_job(self, job_id: int, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            previous = self.repository.command_result(command["command_id"])
            if previous:
                return previous
            job = self.repository.get_job(job_id)
            if not job:
                raise NotFoundError("Automation job not found")
            if job["status"] != "RUNNING":
                raise ConflictError("Automation job is not running")
            success = command.get("success", True)
            if success:
                self.repository.update_job(job_id, {
                    "status": "SUCCEEDED", "result": command.get("result", {}),
                    "completed_at": now(), "lease_expires_at": None,
                })
                owner = self.repository.get_instance_row(job["workflow_instance_id"])
                if owner and owner["execution_status"] == "RUNNING":
                    self._complete_system_step(
                        job["workflow_instance_id"], self.repository.get_step(job["step_instance_id"]),
                        "AUTOMATION_SUCCEEDED", command.get("result", {}),
                    )
            elif job["attempt_count"] < job["max_attempts"]:
                delay = int(command.get("retry_after_seconds", 2 ** job["attempt_count"]))
                self.repository.update_job(job_id, {
                    "status": "RETRY_WAIT", "last_error": command.get("error"),
                    "available_at": (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(),
                    "lease_expires_at": None,
                })
                self.repository.append_event(
                    job["workflow_instance_id"], "AUTOMATION_RETRY_SCHEDULED", actor_id(command),
                    job["step_instance_id"], payload={"job_id": job_id, "error": command.get("error")},
                )
            else:
                self.repository.update_job(job_id, {
                    "status": "FAILED", "last_error": command.get("error"),
                    "completed_at": now(), "lease_expires_at": None,
                })
                self.repository.update_step(
                    job["step_instance_id"], {"state": "FAILED", "execution_status": "FAILED", "completed_at": now()}
                )
                self.repository.append_event(
                    job["workflow_instance_id"], "AUTOMATION_FAILED", actor_id(command),
                    job["step_instance_id"], payload={"job_id": job_id, "error": command.get("error")},
                )
            workflow = self.repository.get_instance_row(job["workflow_instance_id"])
            self.repository.update_workflow(workflow["id"], {"revision": workflow["revision"] + 1})
            self._drive(workflow["id"])
            result = self.get_workflow(workflow["id"])
            self.repository.record_command(command["command_id"], workflow["id"], "complete_job", result)
            return result

    def process_due_timers(self) -> int:
        with self.repository.transaction():
            timers = self.repository.due_timers()
            for timer in timers:
                self.repository.fire_timer(timer["id"])
                if timer["action"] == "COMPLETE_STEP" and timer.get("step_instance_id"):
                    step = self.repository.get_step(timer["step_instance_id"])
                    if step and step["execution_status"] == "WAITING":
                        self._complete_system_step(
                            timer["workflow_instance_id"], step, "TIMER_FIRED", timer.get("payload", {})
                        )
                        self._drive(timer["workflow_instance_id"])
            return len(timers)

    def _cancel_execution(self, workflow_id: int, actor: str, reason: str | None) -> None:
        """Cancel a workflow's open work, then every running descendant."""
        self.repository.cancel_open_work(workflow_id)
        for child_id in self.repository.running_child_ids(workflow_id):
            child = self.repository.get_instance_row(child_id)
            self.repository.update_workflow(child_id, {
                "execution_status": "CANCELLED", "status": "CANCELLED",
                "cancelled_at": now(), "revision": child["revision"] + 1,
            })
            self.repository.append_event(
                child_id, "WORKFLOW_CANCELLED_BY_PARENT", actor,
                payload={"parent_workflow_instance_id": workflow_id, "reason": reason},
            )
            self._cancel_execution(child_id, actor, reason)

    def _can_activate(self, workflow: dict[str, Any], step: dict[str, Any]) -> bool:
        incoming = self.repository.incoming(workflow["id"], step["step_definition_id"])
        if not incoming:
            return True
        applicable = [row for row in incoming if evaluate(row.get("condition"), workflow["variables"])]
        if not applicable:
            return False
        if step["step_type"] == "JOIN":
            rule = step.get("join_rule") or "ALL"
            satisfied = sum(row["execution_status"] in SATISFIED_EXECUTION for row in applicable)
            if rule == "ANY":
                return satisfied >= 1
            if rule == "N_OF_M":
                return satisfied >= int(step.get("configuration", {}).get("required_count", len(applicable)))
        return all(row["execution_status"] in SATISFIED_EXECUTION for row in applicable)

    def _activate(self, workflow: dict[str, Any], step: dict[str, Any]) -> None:
        transition = self.repository.find_fsm_transition(
            step["step_fsm_version_id"], step["state"], "activate"
        )
        state = transition["to_state"] if transition else "READY"
        self.repository.update_step(
            step["id"], {"state": state, "execution_status": "READY", "activated_at": now()}
        )
        self.repository.append_event(
            workflow["id"], "STEP_ACTIVATED", "engine", step["id"], step["state"], state
        )
        if step.get("stage"):
            self.repository.update_workflow(workflow["id"], {"current_stage": step["stage"]})
        config = step.get("configuration", {})
        self.repository.create_candidates(step["id"], config.get("candidates", []))
        if step.get("assignment_role"):
            self.repository.create_assignment(step["id"], "ROLE", step["assignment_role"], assigned_by="engine")

    def _complete_system_step(self, workflow_id: int, step: dict[str, Any],
                              event_type: str, result: dict[str, Any] | None = None) -> None:
        transition = self.repository.find_fsm_transition(
            step["step_fsm_version_id"], step["state"], "complete"
        )
        target = transition["to_state"] if transition else "COMPLETED"
        stamp = now()
        self.repository.update_step(
            step["id"], {"state": target, "execution_status": "COMPLETED",
                         "started_at": step.get("started_at") or stamp,
                         "completed_at": stamp, "result": result or {}}
        )
        self.repository.append_event(
            workflow_id, event_type, "engine", step["id"], step["state"], target, result or {}
        )

    def _complete_workflow(self, workflow: dict[str, Any], end_step: dict[str, Any]) -> None:
        version = self.repository.get_published_version(workflow["workflow_version_id"])
        transition = self.repository.find_fsm_transition(
            version["lifecycle_fsm_version_id"], workflow["lifecycle_state"], "complete"
        )
        lifecycle = transition["to_state"] if transition else workflow["lifecycle_state"]
        stamp = now()
        self.repository.update_workflow(
            workflow["id"], {"status": "COMPLETED", "execution_status": "COMPLETED", "lifecycle_state": lifecycle,
                             "current_stage": "End", "completed_at": stamp}
        )
        self.repository.append_event(
            workflow["id"], "WORKFLOW_COMPLETED", "engine", end_step["id"],
            workflow["lifecycle_state"], lifecycle,
        )

    def _drive(self, workflow_id: int) -> None:
        for _ in range(200):
            changed = False
            workflow = self.repository.get_instance_row(workflow_id)
            if not workflow or workflow["execution_status"] != "RUNNING":
                return
            for step in self.repository.list_steps_by_execution(workflow_id, "NOT_READY"):
                if self._can_activate(workflow, step):
                    self._activate(workflow, step)
                    changed = True
            for step in self.repository.list_steps_by_execution(workflow_id, "READY"):
                step_type = step["step_type"]
                if step_type in IMMEDIATE_TYPES:
                    self._complete_system_step(workflow_id, step, "SYSTEM_STEP_COMPLETED")
                    changed = True
                elif step_type == "END":
                    self._complete_system_step(workflow_id, step, "SYSTEM_STEP_COMPLETED")
                    self._complete_workflow(workflow, step)
                    self._drive_parent(workflow)
                    return
                elif step_type == "WAIT_SIGNAL":
                    config = step.get("configuration", {})
                    self.repository.update_step(
                        step["id"], {"state": "WAITING", "execution_status": "WAITING",
                                     "started_at": now()}
                    )
                    signal = self.repository.unconsumed_signal(
                        workflow_id, config["signal_type"], config.get("correlation_key")
                    )
                    if signal:
                        self.repository.consume_signal(signal["id"], step["id"])
                        self._complete_system_step(
                            workflow_id, self.repository.get_step(step["id"]),
                            "SIGNAL_CONSUMED", signal.get("payload", {})
                        )
                    changed = True
                elif step_type == "AUTOMATED_TASK":
                    self.repository.create_automation_job(workflow_id, step)
                    self.repository.update_step(
                        step["id"], {"state": "QUEUED", "execution_status": "WAITING",
                                     "started_at": now()}
                    )
                    self.repository.append_event(
                        workflow_id, "AUTOMATION_QUEUED", "engine", step["id"], step["state"], "QUEUED"
                    )
                    changed = True
                elif step_type == "TIMER":
                    self.repository.create_timer(workflow_id, step)
                    self.repository.update_step(
                        step["id"], {"state": "WAITING", "execution_status": "WAITING",
                                     "started_at": now()}
                    )
                    self.repository.append_event(
                        workflow_id, "TIMER_SCHEDULED", "engine", step["id"], step["state"], "WAITING"
                    )
                    changed = True
                elif step_type == "SUBWORKFLOW":
                    self.repository.update_step(
                        step["id"], {"state": "WAITING", "execution_status": "WAITING",
                                     "started_at": now()}
                    )
                    config = step.get("configuration", {})
                    if config.get("child_workflow_version_id"):
                        self.start_workflow({
                            "command_id": f"subworkflow:{workflow_id}:{step['id']}:1",
                            "workflow_version_id": config["child_workflow_version_id"],
                            "title": config.get("title", step.get("step_key", "Child workflow")),
                            "business_type": config.get("business_type"),
                            "business_key": config.get("business_key"),
                            "correlation_id": workflow["correlation_id"],
                            "variables": config.get("variables", {}),
                            "actor": "engine",
                            "parent_workflow_instance_id": workflow_id,
                            "root_workflow_instance_id": workflow["root_workflow_instance_id"],
                            "parent_step_instance_id": step["id"],
                            "relationship_type": config.get("relationship_type", "SUBWORKFLOW"),
                            "relationship_key": config.get("relationship_key", step.get("step_key")),
                            "required": config.get("required", True),
                            "subjects": [],
                        })
                    changed = True
            for step in self.repository.list_steps_by_execution(workflow_id, "WAITING"):
                if step["step_type"] == "WAIT_SIGNAL":
                    config = step.get("configuration", {})
                    signal = self.repository.unconsumed_signal(
                        workflow_id, config["signal_type"], config.get("correlation_key")
                    )
                    if signal:
                        self.repository.consume_signal(signal["id"], step["id"])
                        self._complete_system_step(
                            workflow_id, step, "SIGNAL_CONSUMED", signal.get("payload", {})
                        )
                        changed = True
                elif step["step_type"] == "AUTOMATED_TASK":
                    job = self.repository.job_for_step(step["id"])
                    if job and job["status"] == "SUCCEEDED":
                        self._complete_system_step(
                            workflow_id, step, "AUTOMATION_SUCCEEDED", job.get("result") or {}
                        )
                        changed = True
                elif step["step_type"] == "SUBWORKFLOW":
                    children = [
                        item for item in self.repository.get_workflow(workflow_id)["children"]
                        if item.get("parent_step_instance_id") == step["id"]
                    ]
                    required = [item for item in children if item["required_flag"]]
                    awaited = required or children
                    if awaited and all(item["execution_status"] == "COMPLETED" for item in awaited):
                        self._complete_system_step(workflow_id, step, "SUBWORKFLOWS_COMPLETED")
                        changed = True
                    elif any(item["execution_status"] in {"FAILED", "CANCELLED"} for item in required):
                        policy = step.get("configuration", {}).get("child_failure_policy", "FAIL")
                        if policy == "FAIL":
                            self.repository.update_step(
                                step["id"], {"state": "FAILED", "execution_status": "FAILED", "completed_at": now()}
                            )
                            self.repository.append_event(
                                workflow_id, "SUBWORKFLOW_FAILED", "engine", step["id"]
                            )
                            changed = True
            if not changed:
                return
        raise RuntimeError("Workflow did not reach a stable state")

    def _drive_parent(self, workflow: dict[str, Any]) -> None:
        parent_id = workflow.get("parent_workflow_instance_id")
        if parent_id:
            parent = self.repository.get_instance_row(parent_id)
            if parent and parent["execution_status"] == "RUNNING":
                self._drive(parent_id)
