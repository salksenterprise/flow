from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .actor import Actor
from .calendars import deadline
from .errors import ConflictError, ExecutionError, NotFoundError, ValidationError
from .ports import WorkflowRepository
from .rules import evaluate
from .states import (
    SATISFIED_EXECUTION, TERMINAL_EXECUTION, resolve_category,
)
from .validation import validate_template


IMMEDIATE_TYPES = {"FORK", "JOIN", "MILESTONE"}

# The driver runs to a fixed point. The bound turns a definition that
# oscillates into a reported error rather than a hung transaction.
DRIVER_ITERATION_LIMIT = 200


def stamp(moment: datetime) -> str:
    """One timestamp format; see workflow_sqlite.utcnow."""
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now() -> str:
    return stamp(datetime.now(timezone.utc))


def actor_of(command: dict[str, Any]) -> Actor:
    return Actor.from_value(command.get("actor", "system"))


def actor_id(command: dict[str, Any]) -> str:
    return actor_of(command).actor_id


def actor_org(command: dict[str, Any]) -> str | None:
    return actor_of(command).organization_id


def actor_permissions(command: dict[str, Any]) -> frozenset[str]:
    return actor_of(command).permissions


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

    def _replay(self, command_id: str) -> dict[str, Any] | None:
        """The current workflow for a command already applied, or None.

        A repeated command has one effect, and the caller is handed the
        workflow as it stands now. Flow stores a receipt rather than a frozen
        copy of the response, so a replay reflects reality rather than a
        snapshot that may be many revisions stale.
        """
        receipt = self.repository.command_result(command_id)
        if not receipt:
            return None
        return self.get_workflow(receipt["workflow_instance_id"])

    def migrate_workflow_version(self, workflow_id: int, command: dict[str, Any]) -> dict[str, Any]:
        """Move a running instance onto another published version of its definition.

        The policy is deliberately narrow, because a silent remapping of
        in-flight work is worse than a refusal:

        1. It is an explicit, permissioned, reasoned command. Nothing migrates
           on its own, and publishing a new version never disturbs a run.
        2. Only within the same workflow definition, and only to a published
           version.
        3. Refused while any node is active or waiting. Work in someone's hands
           has no defined meaning in a graph that may no longer contain it.
        4. Nodes map by step key. Shared keys keep their state, removed nodes
           are retired, new nodes start unready.
        5. Refused if the current lifecycle state does not exist in the target
           lifecycle FSM, which would otherwise strand the workflow.
        """
        with self.repository.transaction():
            previous = self._replay(command["command_id"])
            if previous:
                return previous
            if "workflow.migrate" not in actor_permissions(command):
                raise ConflictError("Migration requires the 'workflow.migrate' permission")
            if not command.get("reason"):
                raise ValidationError("A reason is required to migrate a workflow version")

            workflow = self.repository.get_instance_row(workflow_id)
            if not workflow:
                raise NotFoundError("Workflow instance not found")
            self._validate_revision(workflow, command)
            if workflow["execution_status"] not in {"RUNNING", "SUSPENDED"}:
                raise ConflictError(
                    f"A {workflow['execution_status'].lower()} workflow cannot be migrated")

            target = self.repository.get_published_version(command["target_version_id"])
            if not target:
                raise ValidationError("Target workflow version is not published")
            source = self.repository.get_published_version(workflow["workflow_version_id"])
            if target["definition_id"] != source["definition_id"]:
                raise ValidationError(
                    "A workflow can only migrate between versions of its own definition")
            if target["id"] == workflow["workflow_version_id"]:
                raise ConflictError("The workflow is already on that version")

            for status in ("ACTIVE", "WAITING"):
                in_flight = self.repository.list_steps_by_execution(workflow_id, status)
                if in_flight:
                    raise ConflictError(
                        "Cannot migrate while work is in flight: "
                        + ", ".join(sorted(step["step_key"] for step in in_flight)))

            if not self.repository.fsm_has_state(
                    target["lifecycle_fsm_version_id"], workflow["lifecycle_state"]):
                raise ConflictError(
                    f"Target version has no lifecycle state '{workflow['lifecycle_state']}'")

            summary = self.repository.remap_step_instances(workflow_id, target["id"])
            self.repository.update_workflow(workflow_id, {"workflow_version_id": target["id"]})
            self.repository.append_event(
                workflow_id, "WORKFLOW_VERSION_MIGRATED", actor_id(command),
                payload={"from_version_id": source["id"], "to_version_id": target["id"],
                         "reason": command["reason"], **summary},
                actor_org_id=actor_org(command),
            )
            self._drive(workflow_id)
            current = self.repository.get_instance_row(workflow_id)
            self.repository.update_workflow(workflow_id, {"revision": current["revision"] + 1})
            result = self.get_workflow(workflow_id)
            self.repository.record_command(
                command["command_id"], workflow_id, "migrate_version", result["revision"])
            return result

    def list_work(self, **filters: Any) -> dict[str, Any]:
        return self.repository.list_work(**filters)

    def stuck_workflows(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.repository.stuck_workflows(limit)

    def operational_counters(self) -> dict[str, int]:
        return self.repository.operational_counters()

    def dead_letter_events(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.repository.dead_letter_events(limit)

    def redrive_event(self, outbox_id: int) -> bool:
        return self.repository.redrive_outbox(outbox_id)

    def repair_step(self, step_id: int, command: dict[str, Any]) -> dict[str, Any]:
        """An authorized, audited manual intervention on a node.

        Repair deliberately bypasses the step FSM, because it exists for the
        situations the definition did not anticipate. That is why it demands an
        explicit permission and a reason, and records every use as an event.
        """
        with self.repository.transaction():
            previous = self._replay(command["command_id"])
            if previous:
                return previous
            if "workflow.repair" not in actor_permissions(command):
                raise ConflictError("Repair requires the 'workflow.repair' permission")
            if not command.get("reason"):
                raise ValidationError("A reason is required to repair a step")
            step = self.repository.get_step(step_id)
            if not step:
                raise NotFoundError("Step instance not found")
            workflow = self.repository.get_instance_row(step["workflow_instance_id"])
            self._validate_revision(workflow, command)

            action = command["action"]
            if action == "skip_step":
                self.repository.update_step(step_id, {
                    "execution_status": "SKIPPED", "completed_at": now()})
            elif action == "force_complete_step":
                self.repository.update_step(step_id, {
                    "execution_status": "COMPLETED", "completed_at": now(),
                    "result": command.get("payload", {})})
            elif action == "retry_step":
                if step["step_type"] == "AUTOMATED_TASK":
                    # Requeue the existing job rather than letting the driver
                    # create a second one for the same node.
                    self.repository.requeue_job_for_step(step_id)
                    self.repository.update_step(step_id, {
                        "execution_status": "WAITING", "completed_at": None, "result": {}})
                else:
                    self.repository.update_step(step_id, {
                        "execution_status": "READY", "completed_at": None, "result": {},
                        "iteration_number": step["iteration_number"] + 1})
            elif action == "reassign_step":
                if not command.get("assignee"):
                    raise ValidationError("reassign_step requires an assignee")
                self.repository.replace_assignments(
                    step_id, command.get("assignee_type", "USER"), command["assignee"],
                    command.get("organization_id"), actor_id(command), command["reason"])
            else:
                raise ValidationError(f"Unknown repair action: {action!r}")

            if action != "reassign_step" and workflow["execution_status"] in {"FAILED", "SUSPENDED"}:
                self.repository.update_workflow(workflow["id"], {
                    "execution_status": "RUNNING", "status": "ACTIVE",
                    "completed_at": None, "suspended_at": None})

            self.repository.append_event(
                workflow["id"], f"REPAIR_{action.upper()}", actor_id(command), step_id,
                payload={"reason": command["reason"], "assignee": command.get("assignee")},
                actor_org_id=actor_org(command),
            )
            self._drive(workflow["id"])
            current = self.repository.get_instance_row(workflow["id"])
            self.repository.update_workflow(workflow["id"], {"revision": current["revision"] + 1})
            result = self.get_workflow(workflow["id"])
            self.repository.record_command(
                command["command_id"], workflow["id"], action, result["revision"])
            return result

    def _validate_revision(self, workflow: dict[str, Any], command: dict[str, Any]) -> None:
        expected = command.get("expected_revision")
        if expected is not None and expected != workflow["revision"]:
            raise ConflictError(f"Workflow revision is {workflow['revision']}; expected {expected}")

    def start_workflow(self, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            previous = self._replay(command["command_id"])
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
            self.repository.record_command(command["command_id"], workflow_id, "start_workflow", result["revision"])
            return result

    def start_child_workflow(self, parent_id: int, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            previous = self._replay(command["command_id"])
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
            previous_result = self._replay(command["command_id"])
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
                    command["command_id"], workflow["id"], "override_join", result["revision"]
                )
                return result
            if command["action"] == "claim" and not self.repository.candidate_allowed(
                    step_id, actor_of(command).as_dict()):
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
            meta = self.repository.state_meta(step["step_fsm_version_id"], target)
            category = resolve_category(target, meta["terminal"], meta["category"])
            values: dict[str, Any] = {"state": target, "execution_status": category}
            if command["action"] in {"start", "resume"}:
                values["started_at"] = step.get("started_at") or now()
                self.repository.record_attempt_start(step_id, step["iteration_number"])
            if meta["terminal"]:
                values["completed_at"] = now()
                values["result"] = command.get("payload", {})
                self.repository.record_attempt_end(
                    step_id, step["iteration_number"], target, command.get("payload", {})
                )
            elif command["action"] == "reopen":
                values.update({
                    "iteration_number": step["iteration_number"] + 1,
                    "completed_at": None, "result": {},
                })
            self.repository.update_step(step_id, values)
            if category == "FAILED":
                self._handle_step_failure(workflow["id"], self.repository.get_step(step_id))
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
            self.repository.record_command(command["command_id"], workflow["id"], command["action"], result["revision"])
            self._drive_parent(workflow)
            return result

    def apply_workflow_action(self, workflow_id: int, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            previous = self._replay(command["command_id"])
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
            self.repository.record_command(command["command_id"], workflow_id, action, result["revision"])
            self._drive_parent(workflow)
            return result

    def update_facts(self, workflow_id: int, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            previous = self._replay(command["command_id"])
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
            self.repository.record_command(command["command_id"], workflow_id, "update_facts", result["revision"])
            return result

    def receive_signal(self, workflow_id: int, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            previous = self._replay(command["command_id"])
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
            self.repository.record_command(command["command_id"], workflow_id, "signal", result["revision"])
            return result

    def ingest_external_event(self, workflow_id: int, event: dict[str, Any]) -> dict[str, Any]:
        """Persist and idempotently translate a connector event into a workflow signal."""
        with self.repository.transaction():
            existing = self.repository.inbox_event(
                event["connector_name"], event["provider_event_id"]
            )
            command_id = f"inbox:{event['connector_name']}:{event['provider_event_id']}"
            if existing and existing["status"] == "PROCESSED":
                return self._replay(command_id) or self.get_workflow(workflow_id)
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
            previous = self._replay(command["command_id"])
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
                    job["step_instance_id"], {"execution_status": "FAILED", "completed_at": now()}
                )
                self.repository.append_event(
                    job["workflow_instance_id"], "AUTOMATION_FAILED", actor_id(command),
                    job["step_instance_id"], payload={"job_id": job_id, "error": command.get("error")},
                )
                self._handle_step_failure(
                    job["workflow_instance_id"],
                    self.repository.get_step(job["step_instance_id"]))
            workflow = self.repository.get_instance_row(job["workflow_instance_id"])
            self.repository.update_workflow(workflow["id"], {"revision": workflow["revision"] + 1})
            self._drive(workflow["id"])
            result = self.get_workflow(workflow["id"])
            self.repository.record_command(command["command_id"], workflow["id"], "complete_job", result["revision"])
            return result

    def process_due_timers(self) -> int:
        with self.repository.transaction():
            timers = self.repository.due_timers()
            for timer in timers:
                self.repository.fire_timer(timer["id"])
                step = (self.repository.get_step(timer["step_instance_id"])
                        if timer.get("step_instance_id") else None)
                if timer["action"] == "COMPLETE_STEP":
                    if step and step["execution_status"] == "WAITING":
                        self._complete_system_step(
                            timer["workflow_instance_id"], step, "TIMER_FIRED",
                            timer.get("payload", {}))
                        self._drive(timer["workflow_instance_id"])
                elif timer["action"] == "SLA_BREACH" and step:
                    self._handle_sla_breach(timer["workflow_instance_id"], step)
            return len(timers)

    def _handle_sla_breach(self, workflow_id: int, step: dict[str, Any]) -> None:
        """A node passed its due time while still open.

        The breach is always recorded. What follows is the definition's choice,
        because whether a late review escalates, fails or merely gets noticed is
        a business decision rather than an engine one.
        """
        if step["execution_status"] in TERMINAL_EXECUTION:
            return
        config = step.get("configuration", {})
        action = config.get("on_breach", "NOTIFY")
        self.repository.append_event(
            workflow_id, "STEP_SLA_BREACHED", "engine", step["id"],
            payload={"on_breach": action, "step_key": step.get("step_key")})
        if action == "ESCALATE" and config.get("escalate_to"):
            self.repository.replace_assignments(
                step["id"], config.get("escalate_to_type", "ROLE"), config["escalate_to"],
                assigned_by="engine", reason="Service level breached")
        elif action == "FAIL":
            self.repository.update_step(
                step["id"], {"execution_status": "FAILED", "completed_at": now()})
            self._handle_step_failure(workflow_id, self.repository.get_step(step["id"]))
            self._drive(workflow_id)

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

    @staticmethod
    def _satisfied(row: dict[str, Any]) -> bool:
        """Whether a predecessor lets its successors proceed.

        A node that failed under a CONTINUE policy counts as satisfied: the
        policy says the graph carries on without it.
        """
        if row["execution_status"] in SATISFIED_EXECUTION:
            return True
        return (row["execution_status"] == "FAILED"
                and (row.get("configuration") or {}).get("on_failure") == "CONTINUE")

    def _can_activate(self, workflow: dict[str, Any], step: dict[str, Any]) -> bool:
        incoming = self.repository.incoming(workflow["id"], step["step_definition_id"])
        if not incoming:
            return True
        rule = (step.get("join_rule") or "ALL") if step["step_type"] == "JOIN" else "ALL"
        if rule == "ALL_REQUIRED":
            # Every predecessor, whether or not its edge condition selected it.
            # Branches that were never taken reach SKIPPED on their own, so this
            # waits for the whole fan-in rather than only the live paths.
            return all(self._satisfied(row) for row in incoming)
        applicable = [row for row in incoming
                      if evaluate(row.get("condition"), workflow["variables"])]
        if not applicable:
            return False
        if step["step_type"] == "JOIN":
            satisfied = sum(self._satisfied(row) for row in applicable)
            if rule == "ANY":
                return satisfied >= 1
            if rule == "N_OF_M":
                required = int(step.get("configuration", {}).get("required_count", len(applicable)))
                return satisfied >= required
        return all(self._satisfied(row) for row in applicable)

    def _is_dead(self, workflow: dict[str, Any], step: dict[str, Any]) -> bool:
        """True when no future event can activate this node.

        Every predecessor has reached a terminal execution status and the node
        still cannot activate, so the branch it sits on was not taken. Without
        this a node on an untaken branch stays pending for the life of the
        workflow and any progress count based on it is wrong.
        """
        incoming = self.repository.incoming(workflow["id"], step["step_definition_id"])
        if not incoming:
            return False
        if not all(row["execution_status"] in TERMINAL_EXECUTION for row in incoming):
            return False
        return not self._can_activate(workflow, step)

    def _skip(self, workflow_id: int, step: dict[str, Any]) -> None:
        """Mark a node whose branch can no longer run.

        Execution status only. The FSM state belongs to the definition, and a
        custom step FSM need not declare a skipped state or a way into it.
        """
        self.repository.update_step(
            step["id"], {"execution_status": "SKIPPED", "completed_at": now()})
        self.repository.append_event(
            workflow_id, "STEP_SKIPPED", "engine", step["id"], step["state"], step["state"],
            {"reason": "no applicable path can activate this node"})

    def _handle_step_failure(self, workflow_id: int, step: dict[str, Any]) -> None:
        """Apply the node's declared failure policy.

        Without this a failed node left the workflow RUNNING with nothing able
        to advance it, which is indistinguishable from a workflow that is
        merely waiting.
        """
        policy = (step.get("configuration") or {}).get("on_failure", "FAIL_WORKFLOW")
        if policy == "CONTINUE":
            return
        if policy == "SUSPEND":
            self.repository.update_workflow(
                workflow_id, {"execution_status": "SUSPENDED", "suspended_at": now()})
            self.repository.append_event(
                workflow_id, "WORKFLOW_SUSPENDED_ON_FAILURE", "engine", step["id"],
                payload={"step_key": step.get("step_key")})
            return
        self.repository.update_workflow(workflow_id, {
            "execution_status": "FAILED", "status": "FAILED", "completed_at": now()})
        self.repository.append_event(
            workflow_id, "WORKFLOW_FAILED", "engine", step["id"],
            payload={"step_key": step.get("step_key")})

    def _activate(self, workflow: dict[str, Any], step: dict[str, Any]) -> None:
        transition = self.repository.find_fsm_transition(
            step["step_fsm_version_id"], step["state"], "activate"
        )
        state = transition["to_state"] if transition else "READY"
        meta = self.repository.state_meta(step["step_fsm_version_id"], state)
        self.repository.update_step(
            step["id"], {"state": state, "activated_at": now(),
                         "execution_status": resolve_category(
                             state, meta["terminal"], meta["category"])}
        )
        self.repository.append_event(
            workflow["id"], "STEP_ACTIVATED", "engine", step["id"], step["state"], state
        )
        if step.get("stage"):
            self.repository.update_workflow(workflow["id"], {"current_stage": step["stage"]})
        config = step.get("configuration", {})
        self.repository.create_candidates(step["id"], config.get("candidates", []))
        due_at = None
        if config.get("due_in_seconds"):
            due_at = stamp(deadline(datetime.now(timezone.utc),
                                    int(config["due_in_seconds"]), config.get("calendar")))
            self.repository.create_timer(step_workflow_id := workflow["id"], step,
                                         due_at, "SLA_BREACH")
            self.repository.append_event(
                step_workflow_id, "STEP_DUE_AT_SET", "engine", step["id"],
                payload={"due_at": due_at})
        if step.get("assignment_role"):
            self.repository.create_assignment(
                step["id"], "ROLE", step["assignment_role"], assigned_by="engine",
                due_at=due_at)

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
        for _ in range(DRIVER_ITERATION_LIMIT):
            changed = False
            workflow = self.repository.get_instance_row(workflow_id)
            if not workflow or workflow["execution_status"] != "RUNNING":
                return
            for step in self.repository.list_steps_by_execution(workflow_id, "NOT_READY"):
                if self._can_activate(workflow, step):
                    self._activate(workflow, step)
                    changed = True
                elif self._is_dead(workflow, step):
                    self._skip(workflow_id, step)
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
                    config = step.get("configuration", {})
                    due = deadline(datetime.now(timezone.utc),
                                   int(config.get("delay_seconds", 0)),
                                   config.get("calendar"))
                    self.repository.create_timer(
                        workflow_id, step, stamp(due), "COMPLETE_STEP")
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
                        if policy == "CONTINUE":
                            # The parent carries on once every awaited child has
                            # settled, successfully or not.
                            if all(item["execution_status"] in TERMINAL_EXECUTION
                                   for item in awaited):
                                self._complete_system_step(
                                    workflow_id, step, "SUBWORKFLOWS_SETTLED",
                                    {"child_failure_policy": "CONTINUE"})
                                changed = True
                        else:
                            self.repository.update_step(
                                step["id"], {"execution_status": "FAILED", "completed_at": now()}
                            )
                            self.repository.append_event(
                                workflow_id, "SUBWORKFLOW_FAILED", "engine", step["id"]
                            )
                            self._handle_step_failure(
                                workflow_id, self.repository.get_step(step["id"]))
                            changed = True
            if not changed:
                return
        raise ExecutionError(
            f"Workflow {workflow_id} did not reach a stable state within "
            f"{DRIVER_ITERATION_LIMIT} driver iterations")

    def _drive_parent(self, workflow: dict[str, Any]) -> None:
        parent_id = workflow.get("parent_workflow_instance_id")
        if parent_id:
            parent = self.repository.get_instance_row(parent_id)
            if parent and parent["execution_status"] == "RUNNING":
                self._drive(parent_id)
