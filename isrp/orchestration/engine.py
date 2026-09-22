from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .actor import Actor
from .calendars import deadline
from .errors import ConflictError, ExecutionError, NotFoundError, ValidationError
from .ports import WorkflowRepository
from .rules import evaluate
from .states import (
    ASSESSMENT, Owner, REQUEST, SATISFIED_EXECUTION, TERMINAL_EXECUTION,
    resolve_category,
)
from .validation import validate_template


IMMEDIATE_TYPES = {"FORK", "JOIN", "MILESTONE"}

# The driver runs to a fixed point. The bound turns a definition that
# oscillates into a reported error rather than a hung transaction.
DRIVER_ITERATION_LIMIT = 200


def stamp(moment: datetime) -> str:
    """One timestamp format; see store.utcnow."""
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


def command_fingerprint(operation: str, target: Any, command: dict[str, Any]) -> str:
    """Bind an idempotency key to the operation, target, and semantic request.

    The optimistic revision is deliberately excluded: a network retry carries
    the revision from the first attempt, even though that attempt advanced it.
    """
    payload = {}
    for key, value in command.items():
        if key in {"command_id", "expected_revision"}:
            continue
        if key == "actor":
            value = Actor.from_value(value).as_dict()
        elif isinstance(value, Actor):
            value = value.as_dict()
        payload[key] = value
    try:
        encoded = json.dumps(
            {"operation": operation, "target": target, "command": payload},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValidationError(
            "Command values must be JSON-serializable for durable idempotency") from error
    return hashlib.sha256(encoded).hexdigest()


class WorkflowEngine:
    """Drives ISRP's two aggregates through their published workflow versions.

    Every entry point that acts on a run names the aggregate by owner: the
    pair ("ISRP_REQUEST", id) or ("ISRP_ASSESSMENT", id). There is no workflow
    instance to name instead.
    """

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

    def list_requests(self) -> list[dict[str, Any]]:
        return self.repository.list_aggregates(REQUEST)

    def list_assessments(self) -> list[dict[str, Any]]:
        return self.repository.list_aggregates(ASSESSMENT)

    def get_aggregate(self, owner: Any) -> dict[str, Any]:
        result = self.repository.get_aggregate(owner)
        if not result:
            raise NotFoundError(f"{owner[0]} {owner[1]} not found")
        return result

    def get_request(self, request_id: int) -> dict[str, Any]:
        return self.get_aggregate(Owner(REQUEST, request_id))

    def get_assessment(self, assessment_id: int) -> dict[str, Any]:
        return self.get_aggregate(Owner(ASSESSMENT, assessment_id))

    def _replay(self, command_id: str, operation: str,
                request_fingerprint: str) -> dict[str, Any] | None:
        """The current aggregate for a command already applied, or None.

        A repeated command has one effect, and the caller is handed the
        aggregate as it stands now. A receipt is stored rather than a frozen
        copy of the response, so a replay reflects reality rather than a
        snapshot that may be many revisions stale.
        """
        if not isinstance(command_id, str) or not command_id.strip():
            raise ValidationError("command_id must be a non-empty string")
        receipt = self.repository.command_result(command_id)
        if not receipt:
            return None
        stored = receipt.get("request_fingerprint")
        if stored is None:
            raise ConflictError(
                "The command_id belongs to a legacy receipt that was not bound "
                "to its request; retry with a new command_id")
        if stored != request_fingerprint:
            raise ConflictError(
                "The command_id was already used for a different operation, "
                "target, or request payload")
        return self.get_aggregate(Owner(receipt["owner_type"], receipt["owner_id"]))

    def migrate_version(self, owner: Any, command: dict[str, Any]) -> dict[str, Any]:
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
        5. Refused if the current lifecycle status does not exist in the target
           lifecycle FSM, which would otherwise strand the aggregate.
        """
        owner = Owner(*owner)
        with self.repository.transaction():
            operation = "migrate_version"
            fingerprint = command_fingerprint(operation, owner, command)
            previous = self._replay(command["command_id"], operation, fingerprint)
            if previous:
                return previous
            if "workflow.migrate" not in actor_permissions(command):
                raise ConflictError("Migration requires the 'workflow.migrate' permission")
            if not command.get("reason"):
                raise ValidationError("A reason is required to migrate a workflow version")

            aggregate = self.repository.get_aggregate_row(owner)
            if not aggregate:
                raise NotFoundError(f"{owner.type} {owner.id} not found")
            self._validate_revision(aggregate, command)
            if aggregate["execution_status"] not in {"RUNNING", "SUSPENDED"}:
                raise ConflictError(
                    f"A {aggregate['execution_status'].lower()} aggregate cannot be migrated")

            target = self.repository.get_published_version(command["target_version_id"])
            if not target:
                raise ValidationError("Target workflow version is not published")
            source = self.repository.get_published_version(aggregate["workflow_version_id"])
            if target["definition_id"] != source["definition_id"]:
                raise ValidationError(
                    "A workflow can only migrate between versions of its own definition")
            if target["id"] == aggregate["workflow_version_id"]:
                raise ConflictError("It is already on that version")

            for status in ("ACTIVE", "WAITING"):
                in_flight = self.repository.list_steps_by_execution(owner, status)
                if in_flight:
                    raise ConflictError(
                        "Cannot migrate while work is in flight: "
                        + ", ".join(sorted(step["step_key"] for step in in_flight)))

            if not self.repository.fsm_has_state(
                    target["lifecycle_fsm_version_id"], aggregate["lifecycle_status"]):
                raise ConflictError(
                    f"Target version has no lifecycle status '{aggregate['lifecycle_status']}'")

            summary = self.repository.remap_step_instances(owner, target["id"])
            self.repository.update_aggregate(owner, {"workflow_version_id": target["id"]})
            self.repository.append_event(
                owner, "WORKFLOW_VERSION_MIGRATED", actor_id(command),
                payload={"from_version_id": source["id"], "to_version_id": target["id"],
                         "reason": command["reason"], **summary},
                actor_org_id=actor_org(command),
            )
            self._drive(owner)
            current = self.repository.get_aggregate_row(owner)
            self.repository.update_aggregate(owner, {"revision": current["revision"] + 1})
            result = self.get_aggregate(owner)
            self.repository.record_command(
                command["command_id"], owner, operation, result["revision"], fingerprint)
            return result

    def list_work(self, **filters: Any) -> dict[str, Any]:
        return self.repository.list_work(**filters)

    # --- the shared reliability surface --------------------------------------
    #
    # ISRP writes its own domain events, outbox rows and provider receipts
    # through these, into the same tables the orchestration uses. One log, one
    # outbox, one inbox, and -- since an execution event is written against the
    # aggregate it drove -- a determination and the step transition it caused
    # land in one transaction, on one stream, in order.

    def record_event(self, aggregate_type: str, aggregate_id: str, event_type: str,
                     actor: Any = "system", **fields: Any) -> dict[str, Any]:
        """Append a domain event to the shared log and enqueue it for delivery.

        There is no reserved aggregate type. A request's domain events and the
        orchestration events that moved it deliberately share one sequence.

        Pass `publish=False` for an event that is audit-only and should not be
        delivered anywhere.
        """
        if not aggregate_type:
            raise ValidationError("A domain event needs an aggregate_type")
        if not aggregate_id:
            raise ValidationError("A domain event needs an aggregate_id")
        if not event_type:
            raise ValidationError("A domain event needs an event_type")
        resolved = Actor.from_value(actor)
        fields.setdefault("actor_org_id", resolved.organization_id)
        with self.repository.transaction():
            return self.repository.append_domain_event(
                aggregate_type, aggregate_id, event_type, resolved.actor_id, **fields)

    def list_events(self, aggregate_type: str, aggregate_id: str,
                    limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        """One aggregate's ordered history: domain and execution together."""
        return self.repository.list_events(aggregate_type, aggregate_id, limit, offset)

    def record_inbox_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Durably receive a provider event, deduplicated on connector and id.

        Unlike ingest_external_event this does not require an aggregate, so a
        connector can accept a provider event that has not yet been correlated
        to one.
        """
        for field in ("connector_name", "provider_event_id", "event_type"):
            if not event.get(field):
                raise ValidationError(f"An inbox event needs {field}")
        with self.repository.transaction():
            return self.repository.record_inbox(event)

    def claim_inbox_events(self, worker_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return self.repository.claim_inbox_events(worker_id, limit)

    def complete_inbox_event(self, inbox_id: int, worker_id: str,
                             error: str | None = None) -> None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValidationError("worker_id must be a non-empty string")
        with self.repository.transaction():
            if not self.repository.mark_inbox_processed(inbox_id, error, worker_id):
                raise ConflictError(
                    "Inbox event is not leased to this worker or is no longer processing")

    def stuck_aggregates(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.repository.stuck_aggregates(limit)

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
            operation = command["action"]
            fingerprint = command_fingerprint(operation, {"step_id": step_id}, command)
            previous = self._replay(command["command_id"], operation, fingerprint)
            if previous:
                return previous
            if "workflow.repair" not in actor_permissions(command):
                raise ConflictError("Repair requires the 'workflow.repair' permission")
            if not command.get("reason"):
                raise ValidationError("A reason is required to repair a step")
            step = self.repository.get_step(step_id)
            if not step:
                raise NotFoundError("Step instance not found")
            owner = Owner(step["owner_type"], step["owner_id"])
            aggregate = self.repository.get_aggregate_row(owner)
            self._validate_revision(aggregate, command)

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
                    if not self.repository.requeue_job_for_step(step_id):
                        raise ConflictError("The automation step has no job to retry")
                    self.repository.update_step(step_id, {
                        "execution_status": "WAITING", "completed_at": None, "result": {}})
                else:
                    self.repository.update_step(step_id, {
                        "execution_status": "READY", "completed_at": None, "result": {},
                        "iteration_number": step["iteration_number"] + 1})
            elif action == "reassign_step":
                if not command.get("assignee"):
                    raise ValidationError("reassign_step requires an assignee")
                if step["execution_status"] in TERMINAL_EXECUTION:
                    raise ConflictError("A terminal step cannot be reassigned")
                self.repository.replace_assignments(
                    step_id, command.get("assignee_type", "USER"), command["assignee"],
                    command.get("organization_id"), actor_id(command), command["reason"])
            else:
                raise ValidationError(f"Unknown repair action: {action!r}")

            if action != "reassign_step" and aggregate["execution_status"] in {"FAILED", "SUSPENDED"}:
                self.repository.update_aggregate(owner, {
                    "execution_status": "RUNNING",
                    "completed_at": None, "suspended_at": None})

            self.repository.append_event(
                owner, f"REPAIR_{action.upper()}", actor_id(command), step_id,
                payload={"reason": command["reason"], "assignee": command.get("assignee")},
                actor_org_id=actor_org(command),
            )
            self._drive(owner)
            current = self.repository.get_aggregate_row(owner)
            self.repository.update_aggregate(owner, {"revision": current["revision"] + 1})
            result = self.get_aggregate(owner)
            self.repository.record_command(
                command["command_id"], owner, action, result["revision"], fingerprint)
            return result

    def _validate_revision(self, aggregate: dict[str, Any], command: dict[str, Any]) -> None:
        expected = command.get("expected_revision")
        if expected is not None and expected != aggregate["revision"]:
            raise ConflictError(f"Revision is {aggregate['revision']}; expected {expected}")

    def _start(self, owner_type: str, command: dict[str, Any]) -> dict[str, Any]:
        operation = f"start_{owner_type.lower()}"
        target = {"owner_type": owner_type, "request_id": command.get("request_id")}
        fingerprint = command_fingerprint(operation, target, command)
        previous = self._replay(command["command_id"], operation, fingerprint)
        if previous:
            return previous
        version = self.repository.get_published_version(command["workflow_version_id"])
        if not version:
            raise ValidationError("Published workflow version not found")
        owner = self.repository.create_aggregate(owner_type, command, version)
        self.repository.create_step_instances(owner, version["id"])
        self.repository.append_event(
            owner, "WORKFLOW_STARTED", actor_id(command),
            payload={"workflow_version_id": version["id"]},
            actor_org_id=actor_org(command),
        )
        self._drive(owner)
        aggregate = self.repository.get_aggregate_row(owner)
        self.repository.update_aggregate(owner, {"revision": aggregate["revision"] + 1})
        result = self.get_aggregate(owner)
        self.repository.record_command(
            command["command_id"], owner, operation, result["revision"], fingerprint)
        return result

    def start_request(self, command: dict[str, Any]) -> dict[str, Any]:
        """Open an information security review request on a published version."""
        with self.repository.transaction():
            return self._start(REQUEST, command)

    def start_assessment(self, request_id: int, command: dict[str, Any]) -> dict[str, Any]:
        """Open an assessment under a request.

        The request is the assessment's parent by foreign key rather than by an
        opaque relationship type and key, so there is nothing to configure and
        nothing that can disagree with the data model.
        """
        with self.repository.transaction():
            operation = f"start_{ASSESSMENT.lower()}"
            target = {"owner_type": ASSESSMENT, "request_id": request_id}
            fingerprint = command_fingerprint(
                operation, target, {**command, "request_id": request_id})
            previous = self._replay(command["command_id"], operation, fingerprint)
            if previous:
                return previous
            request_owner = Owner(REQUEST, request_id)
            request = self.repository.get_aggregate_row(request_owner)
            if not request:
                raise NotFoundError(f"Request {request_id} not found")
            self._validate_revision(request, command)
            if request["execution_status"] != "RUNNING":
                raise ConflictError(
                    f"Cannot start an assessment under a request that is "
                    f"{request['execution_status']}")
            step_id = command.get("parent_step_instance_id")
            if step_id:
                step = self.repository.get_step(step_id)
                if (not step or step["owner_type"] != REQUEST
                        or step["owner_id"] != request_id
                        or step["step_type"] != "ASSESSMENT"):
                    raise ValidationError(
                        "parent_step_instance_id must identify an ASSESSMENT node "
                        "on that request")
            assessment = self._start(ASSESSMENT, {**command, "request_id": request_id})
            refreshed = self.repository.get_aggregate_row(request_owner)
            self.repository.update_aggregate(
                request_owner, {"revision": refreshed["revision"] + 1})
            self.repository.append_event(
                request_owner, "ASSESSMENT_STARTED", actor_id(command), step_id,
                payload={"assessment_id": assessment["id"],
                         "assessment_type": command.get("assessment_type"),
                         "required": command.get("required", True)},
                actor_org_id=actor_org(command),
            )
            self._drive(request_owner)
            return assessment

    def apply_action(self, step_id: int, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            operation = command["action"]
            fingerprint = command_fingerprint(operation, {"step_id": step_id}, command)
            previous_result = self._replay(
                command["command_id"], operation, fingerprint)
            if previous_result:
                return previous_result
            step = self.repository.get_step(step_id)
            if not step:
                raise NotFoundError("Step instance not found")
            owner = Owner(step["owner_type"], step["owner_id"])
            aggregate = self.repository.get_aggregate_row(owner)
            self._validate_revision(aggregate, command)
            if aggregate["execution_status"] != "RUNNING":
                raise ConflictError(f"{owner.type} is {aggregate['execution_status']}")
            if command["action"] == "override_join":
                if step["step_type"] != "JOIN":
                    raise ConflictError("Only a JOIN node can be overridden")
                if "workflow.override" not in actor_permissions(command):
                    raise ConflictError("Join override requires workflow.override permission")
                if not command.get("reason"):
                    raise ValidationError("A reason is required to override a join")
                self._complete_system_step(
                    owner, step, "JOIN_OVERRIDDEN",
                    {"reason": command["reason"], "actor": actor_id(command)},
                )
                self._drive(owner)
                current = self.repository.get_aggregate_row(owner)
                self.repository.update_aggregate(
                    owner, {"revision": current["revision"] + 1}
                )
                result = self.get_aggregate(owner)
                self.repository.record_command(
                    command["command_id"], owner, "override_join", result["revision"],
                    fingerprint,
                )
                return result
            if command["action"] == "claim" and not self.repository.candidate_allowed(
                    step_id, actor_of(command).as_dict()):
                raise ConflictError("Actor is not an eligible candidate for this work")
            if command["action"] in {"assign", "reassign"} and not command.get("assignee"):
                raise ValidationError(f"{command['action']} requires an assignee")
            if command.get("assignee_type", "USER") not in {
                    "USER", "ROLE", "GROUP", "ORGANIZATION"}:
                raise ValidationError("Unsupported assignee_type")
            transition = self.repository.find_fsm_transition(
                step["step_fsm_version_id"], step["state"], command["action"]
            )
            if not transition:
                raise ConflictError(
                    f"Action '{command['action']}' is not allowed from state '{step['state']}'"
                )
            if transition.get("guard") and not evaluate(transition["guard"], aggregate["variables"]):
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
                self._handle_step_failure(owner, self.repository.get_step(step_id))
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
                owner, f"STEP_{command['action'].upper()}", actor_id(command), step_id,
                step["state"], target, command.get("payload", {}), actor_org_id=actor_org(command),
            )
            self._drive(owner)
            current = self.repository.get_aggregate_row(owner)
            self.repository.update_aggregate(owner, {"revision": current["revision"] + 1})
            result = self.get_aggregate(owner)
            self.repository.record_command(
                command["command_id"], owner, command["action"], result["revision"], fingerprint)
            self._drive_request(aggregate)
            return result

    def apply_lifecycle_action(self, owner: Any, command: dict[str, Any]) -> dict[str, Any]:
        """Move an aggregate through its own lifecycle FSM, or suspend it."""
        owner = Owner(*owner)
        with self.repository.transaction():
            operation = command["action"]
            fingerprint = command_fingerprint(operation, owner, command)
            previous = self._replay(command["command_id"], operation, fingerprint)
            if previous:
                return previous
            aggregate = self.repository.get_aggregate_row(owner)
            if not aggregate:
                raise NotFoundError(f"{owner.type} {owner.id} not found")
            self._validate_revision(aggregate, command)
            action = command["action"]
            previous_state = aggregate["lifecycle_status"]
            values: dict[str, Any] = {}
            if action == "suspend":
                if aggregate["execution_status"] != "RUNNING":
                    raise ConflictError("Only a running aggregate can be suspended")
                values = {"execution_status": "SUSPENDED", "suspended_at": now()}
            elif action == "resume":
                if aggregate["execution_status"] != "SUSPENDED":
                    raise ConflictError("Only a suspended aggregate can be resumed")
                values = {"execution_status": "RUNNING", "suspended_at": None}
            elif action == "terminate":
                if not command.get("reason"):
                    raise ValidationError("A reason is required to terminate")
                if aggregate["execution_status"] not in {"RUNNING", "SUSPENDED"}:
                    raise ConflictError(
                        "Only a running or suspended aggregate can be terminated")
                values = {"execution_status": "CANCELLED", "cancelled_at": now()}
            else:
                version = self.repository.get_published_version(aggregate["workflow_version_id"])
                transition = self.repository.find_fsm_transition(
                    version["lifecycle_fsm_version_id"], aggregate["lifecycle_status"], action
                )
                if not transition:
                    raise ConflictError(
                        f"Action '{action}' is not allowed from '{aggregate['lifecycle_status']}'"
                    )
                if transition.get("reason_required") and not command.get("reason"):
                    raise ValidationError("A reason is required for this transition")
                permission = transition.get("required_permission")
                if permission and permission not in actor_permissions(command):
                    raise ConflictError("Lifecycle transition permission denied")
                values["lifecycle_status"] = transition["to_state"]
                if self.repository.state_is_terminal(
                    version["lifecycle_fsm_version_id"], transition["to_state"]
                ):
                    values["execution_status"] = (
                        "CANCELLED" if transition["to_state"] == "CANCELLED" else "COMPLETED"
                    )
                    values["completed_at"] = now()
                elif action == "reopen":
                    values.update({"execution_status": "RUNNING", "completed_at": None})
            values["revision"] = aggregate["revision"] + 1
            self.repository.update_aggregate(owner, values)
            self.repository.append_event(
                owner, f"WORKFLOW_{action.upper()}", actor_id(command),
                previous=previous_state, new=values.get("lifecycle_status", previous_state),
                payload={"reason": command.get("reason")}, actor_org_id=actor_org(command),
            )
            if values.get("execution_status") == "CANCELLED":
                self._cancel_execution(owner, actor_id(command), command.get("reason"))
            if values.get("execution_status") == "RUNNING":
                self._drive(owner)
            result = self.get_aggregate(owner)
            self.repository.record_command(
                command["command_id"], owner, action, result["revision"], fingerprint)
            self._drive_request(aggregate)
            return result

    def update_facts(self, owner: Any, command: dict[str, Any]) -> dict[str, Any]:
        owner = Owner(*owner)
        with self.repository.transaction():
            operation = "update_facts"
            fingerprint = command_fingerprint(operation, owner, command)
            previous = self._replay(command["command_id"], operation, fingerprint)
            if previous:
                return previous
            aggregate = self.repository.get_aggregate_row(owner)
            if not aggregate:
                raise NotFoundError(f"{owner.type} {owner.id} not found")
            self._validate_revision(aggregate, command)
            self.repository.set_facts(
                aggregate, command.get("facts", {}), actor_id(command),
                command.get("source_type", "COMMAND"), command.get("source_reference"),
            )
            self.repository.append_event(
                owner, "WORKFLOW_FACTS_UPDATED", actor_id(command),
                payload={"keys": sorted(command.get("facts", {}))}, actor_org_id=actor_org(command),
            )
            self._drive(owner)
            result = self.get_aggregate(owner)
            self.repository.record_command(
                command["command_id"], owner, operation, result["revision"], fingerprint)
            return result

    def receive_signal(self, owner: Any, command: dict[str, Any]) -> dict[str, Any]:
        owner = Owner(*owner)
        with self.repository.transaction():
            operation = "signal"
            fingerprint = command_fingerprint(operation, owner, command)
            previous = self._replay(command["command_id"], operation, fingerprint)
            if previous:
                return previous
            aggregate = self.repository.get_aggregate_row(owner)
            if not aggregate:
                raise NotFoundError(f"{owner.type} {owner.id} not found")
            self.repository.insert_signal(command, owner)
            if command.get("apply_facts"):
                self.repository.set_facts(
                    aggregate, command.get("payload", {}), actor_id(command), "SIGNAL",
                    command.get("correlation_key"),
                )
            self.repository.append_event(
                owner, "SIGNAL_RECEIVED", actor_id(command),
                payload={"signal_type": command["signal_type"],
                         "correlation_key": command.get("correlation_key")},
                actor_org_id=actor_org(command),
            )
            self._drive(owner)
            current = self.repository.get_aggregate_row(owner)
            self.repository.update_aggregate(owner, {"revision": current["revision"] + 1})
            result = self.get_aggregate(owner)
            self.repository.record_command(
                command["command_id"], owner, operation, result["revision"], fingerprint)
            return result

    def ingest_external_event(self, owner: Any, event: dict[str, Any]) -> dict[str, Any]:
        """Persist and idempotently translate a connector event into a signal."""
        owner = Owner(*owner)
        with self.repository.transaction():
            existing = self.repository.inbox_event(
                event["connector_name"], event["provider_event_id"]
            )
            command_id = f"inbox:{event['connector_name']}:{event['provider_event_id']}"
            signal_command = {
                "command_id": command_id,
                "actor": f"connector:{event['connector_name']}",
                "signal_type": event["event_type"],
                "correlation_key": event.get("correlation_key"),
                "payload": event.get("payload", {}),
                "apply_facts": event.get("apply_facts", False),
            }
            if existing and existing["status"] == "PROCESSED":
                fingerprint = command_fingerprint("signal", owner, signal_command)
                return (self._replay(command_id, "signal", fingerprint)
                        or self.get_aggregate(owner))
            inbox_id = existing["id"] if existing else self.repository.insert_inbox_event(event)
            result = self.receive_signal(owner, signal_command)
            self.repository.mark_inbox_processed(inbox_id)
            return result

    def claim_automation_jobs(self, worker_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self.repository.transaction():
            return self.repository.claim_jobs(worker_id, limit)

    def complete_automation_job(self, job_id: int, command: dict[str, Any]) -> dict[str, Any]:
        with self.repository.transaction():
            operation = "complete_job"
            fingerprint = command_fingerprint(operation, {"job_id": job_id}, command)
            previous = self._replay(command["command_id"], operation, fingerprint)
            if previous:
                return previous
            job = self.repository.get_job(job_id)
            if not job:
                raise NotFoundError("Automation job not found")
            if job["status"] != "RUNNING":
                raise ConflictError("Automation job is not running")
            worker_id = command.get("worker_id")
            if not worker_id or worker_id != job.get("claimed_by"):
                raise ConflictError("Automation job is not leased to this worker")
            if job.get("lease_expires_at") and job["lease_expires_at"] < now():
                raise ConflictError("Automation job lease has expired")
            owner = Owner(job["owner_type"], job["owner_id"])
            success = command.get("success", True)
            if success:
                self.repository.update_job(job_id, {
                    "status": "SUCCEEDED", "result": command.get("result", {}),
                    "completed_at": now(), "lease_expires_at": None,
                })
                current = self.repository.get_aggregate_row(owner)
                if current and current["execution_status"] == "RUNNING":
                    self._complete_system_step(
                        owner, self.repository.get_step(job["step_instance_id"]),
                        "AUTOMATION_SUCCEEDED", command.get("result", {}),
                    )
            elif job["attempt_count"] < job["max_attempts"]:
                delay = command.get("retry_after_seconds", 2 ** job["attempt_count"])
                if (isinstance(delay, bool) or not isinstance(delay, int) or delay < 0):
                    raise ValidationError(
                        "retry_after_seconds must be a non-negative whole number")
                self.repository.update_job(job_id, {
                    "status": "RETRY_WAIT", "last_error": command.get("error"),
                    "available_at": stamp(
                        datetime.now(timezone.utc) + timedelta(seconds=delay)),
                    "lease_expires_at": None,
                })
                self.repository.append_event(
                    owner, "AUTOMATION_RETRY_SCHEDULED", actor_id(command),
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
                    owner, "AUTOMATION_FAILED", actor_id(command),
                    job["step_instance_id"], payload={"job_id": job_id, "error": command.get("error")},
                )
                self._handle_step_failure(
                    owner, self.repository.get_step(job["step_instance_id"]))
            aggregate = self.repository.get_aggregate_row(owner)
            self.repository.update_aggregate(owner, {"revision": aggregate["revision"] + 1})
            self._drive(owner)
            result = self.get_aggregate(owner)
            self.repository.record_command(
                command["command_id"], owner, operation, result["revision"], fingerprint)
            return result

    def process_due_timers(self) -> int:
        with self.repository.transaction():
            timers = self.repository.due_timers()
            touched: set[Owner] = set()
            for timer in timers:
                self.repository.fire_timer(timer["id"])
                owner = Owner(timer["owner_type"], timer["owner_id"])
                touched.add(owner)
                step = (self.repository.get_step(timer["step_instance_id"])
                        if timer.get("step_instance_id") else None)
                if timer["action"] == "COMPLETE_STEP":
                    if step and step["execution_status"] == "WAITING":
                        self._complete_system_step(
                            owner, step, "TIMER_FIRED", timer.get("payload", {}))
                        self._drive(owner)
                elif timer["action"] == "SLA_BREACH" and step:
                    self._handle_sla_breach(owner, step)
            for owner in touched:
                aggregate = self.repository.get_aggregate_row(owner)
                if aggregate:
                    self.repository.update_aggregate(
                        owner, {"revision": aggregate["revision"] + 1})
            return len(timers)

    def _handle_sla_breach(self, owner: Owner, step: dict[str, Any]) -> None:
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
            owner, "STEP_SLA_BREACHED", "engine", step["id"],
            payload={"on_breach": action, "step_key": step.get("step_key")})
        if action == "ESCALATE" and config.get("escalate_to"):
            self.repository.replace_assignments(
                step["id"], config.get("escalate_to_type", "ROLE"), config["escalate_to"],
                assigned_by="engine", reason="Service level breached")
        elif action == "FAIL":
            self.repository.update_step(
                step["id"], {"execution_status": "FAILED", "completed_at": now()})
            self._handle_step_failure(owner, self.repository.get_step(step["id"]))
            self._drive(owner)

    def _cancel_execution(self, owner: Owner, actor: str, reason: str | None) -> None:
        """Cancel an aggregate's open work, and a request's running assessments.

        The recursion the old parent-child machinery needed is gone with it:
        there are two levels, so cancelling a request cancels its assessments
        and that is the end of it.
        """
        self.repository.cancel_open_work(owner)
        if owner.type != REQUEST:
            return
        for assessment_id in self.repository.running_assessment_ids(owner.id):
            assessment = Owner(ASSESSMENT, assessment_id)
            current = self.repository.get_aggregate_row(assessment)
            self.repository.update_aggregate(assessment, {
                "execution_status": "CANCELLED",
                "cancelled_at": now(), "revision": current["revision"] + 1,
            })
            self.repository.append_event(
                assessment, "WORKFLOW_CANCELLED_BY_REQUEST", actor,
                payload={"request_id": owner.id, "reason": reason},
            )
            self.repository.cancel_open_work(assessment)

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

    def _can_activate(self, aggregate: dict[str, Any], step: dict[str, Any]) -> bool:
        incoming = self.repository.incoming(
            (aggregate["owner_type"], aggregate["id"]), step["step_definition_id"])
        if not incoming:
            return True
        rule = (step.get("join_rule") or "ALL") if step["step_type"] == "JOIN" else "ALL"
        if rule == "ALL_REQUIRED":
            # Every predecessor, whether or not its edge condition selected it.
            # Branches that were never taken reach SKIPPED on their own, so this
            # waits for the whole fan-in rather than only the live paths.
            return all(self._satisfied(row) for row in incoming)
        applicable = [row for row in incoming
                      if evaluate(row.get("condition"), aggregate["variables"])]
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

    def _is_dead(self, aggregate: dict[str, Any], step: dict[str, Any]) -> bool:
        """True when no future event can activate this node.

        Every predecessor has reached a terminal execution status and the node
        still cannot activate, so the branch it sits on was not taken. Without
        this a node on an untaken branch stays pending for the life of the
        workflow and any progress count based on it is wrong.
        """
        incoming = self.repository.incoming(
            (aggregate["owner_type"], aggregate["id"]), step["step_definition_id"])
        if not incoming:
            return False
        if not all(row["execution_status"] in TERMINAL_EXECUTION for row in incoming):
            return False
        return not self._can_activate(aggregate, step)

    def _skip(self, owner: Owner, step: dict[str, Any]) -> None:
        """Mark a node whose branch can no longer run.

        Execution status only. The FSM state belongs to the definition, and a
        custom step FSM need not declare a skipped state or a way into it.
        """
        self.repository.update_step(
            step["id"], {"execution_status": "SKIPPED", "completed_at": now()})
        self.repository.append_event(
            owner, "STEP_SKIPPED", "engine", step["id"], step["state"], step["state"],
            {"reason": "no applicable path can activate this node"})

    def _handle_step_failure(self, owner: Owner, step: dict[str, Any]) -> None:
        """Apply the node's declared failure policy.

        Without this a failed node left the run RUNNING with nothing able to
        advance it, which is indistinguishable from one that is merely waiting.
        """
        policy = (step.get("configuration") or {}).get("on_failure", "FAIL_WORKFLOW")
        if policy == "CONTINUE":
            return
        if policy == "SUSPEND":
            self.repository.update_aggregate(
                owner, {"execution_status": "SUSPENDED", "suspended_at": now()})
            self.repository.append_event(
                owner, "WORKFLOW_SUSPENDED_ON_FAILURE", "engine", step["id"],
                payload={"step_key": step.get("step_key")})
            return
        self.repository.update_aggregate(owner, {
            "execution_status": "FAILED", "completed_at": now()})
        self.repository.append_event(
            owner, "WORKFLOW_FAILED", "engine", step["id"],
            payload={"step_key": step.get("step_key")})

    def _activate(self, aggregate: dict[str, Any], step: dict[str, Any]) -> None:
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
        owner = Owner(aggregate["owner_type"], aggregate["id"])
        self.repository.append_event(
            owner, "STEP_ACTIVATED", "engine", step["id"], step["state"], state
        )
        if step.get("stage"):
            self.repository.update_aggregate(owner, {"current_stage": step["stage"]})
        config = step.get("configuration", {})
        self.repository.create_candidates(step["id"], config.get("candidates", []))
        due_at = None
        if config.get("due_in_seconds"):
            due_at = stamp(deadline(datetime.now(timezone.utc),
                                    int(config["due_in_seconds"]), config.get("calendar")))
            self.repository.create_timer(owner, step, due_at, "SLA_BREACH")
            self.repository.append_event(
                owner, "STEP_DUE_AT_SET", "engine", step["id"],
                payload={"due_at": due_at})
        if step.get("assignment_role"):
            self.repository.create_assignment(
                step["id"], "ROLE", step["assignment_role"], assigned_by="engine",
                due_at=due_at)

    def _complete_system_step(self, owner: Owner, step: dict[str, Any],
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
            owner, event_type, "engine", step["id"], step["state"], target, result or {}
        )

    def _complete_run(self, aggregate: dict[str, Any], end_step: dict[str, Any]) -> None:
        owner = Owner(aggregate["owner_type"], aggregate["id"])
        version = self.repository.get_published_version(aggregate["workflow_version_id"])
        transition = self.repository.find_fsm_transition(
            version["lifecycle_fsm_version_id"], aggregate["lifecycle_status"], "complete"
        )
        lifecycle = transition["to_state"] if transition else aggregate["lifecycle_status"]
        stamp = now()
        self.repository.update_aggregate(
            owner, {"execution_status": "COMPLETED", "lifecycle_status": lifecycle,
                    "current_stage": "End", "completed_at": stamp}
        )
        self.repository.append_event(
            owner, "WORKFLOW_COMPLETED", "engine", end_step["id"],
            aggregate["lifecycle_status"], lifecycle,
        )

    def _drive(self, owner: Owner) -> None:
        """Run the graph to a fixed point for one aggregate."""
        for _ in range(DRIVER_ITERATION_LIMIT):
            changed = False
            aggregate = self.repository.get_aggregate_row(owner)
            if not aggregate or aggregate["execution_status"] != "RUNNING":
                return
            for step in self.repository.list_steps_by_execution(owner, "NOT_READY"):
                if self._can_activate(aggregate, step):
                    self._activate(aggregate, step)
                    changed = True
                elif self._is_dead(aggregate, step):
                    self._skip(owner, step)
                    changed = True
            for step in self.repository.list_steps_by_execution(owner, "READY"):
                step_type = step["step_type"]
                if step_type in IMMEDIATE_TYPES:
                    self._complete_system_step(owner, step, "SYSTEM_STEP_COMPLETED")
                    changed = True
                elif step_type == "END":
                    self._complete_system_step(owner, step, "SYSTEM_STEP_COMPLETED")
                    self._complete_run(aggregate, step)
                    self._drive_request(aggregate)
                    return
                elif step_type == "WAIT_SIGNAL":
                    config = step.get("configuration", {})
                    self.repository.update_step(
                        step["id"], {"state": "WAITING", "execution_status": "WAITING",
                                     "started_at": now()}
                    )
                    signal = self.repository.unconsumed_signal(
                        owner, config["signal_type"], config.get("correlation_key")
                    )
                    if signal:
                        self.repository.consume_signal(signal["id"], step["id"])
                        self._complete_system_step(
                            owner, self.repository.get_step(step["id"]),
                            "SIGNAL_CONSUMED", signal.get("payload", {})
                        )
                    changed = True
                elif step_type == "AUTOMATED_TASK":
                    self.repository.create_automation_job(owner, step)
                    self.repository.update_step(
                        step["id"], {"state": "QUEUED", "execution_status": "WAITING",
                                     "started_at": now()}
                    )
                    self.repository.append_event(
                        owner, "AUTOMATION_QUEUED", "engine", step["id"], step["state"], "QUEUED"
                    )
                    changed = True
                elif step_type == "TIMER":
                    config = step.get("configuration", {})
                    due = deadline(datetime.now(timezone.utc),
                                   int(config.get("delay_seconds", 0)),
                                   config.get("calendar"))
                    self.repository.create_timer(owner, step, stamp(due), "COMPLETE_STEP")
                    self.repository.update_step(
                        step["id"], {"state": "WAITING", "execution_status": "WAITING",
                                     "started_at": now()}
                    )
                    self.repository.append_event(
                        owner, "TIMER_SCHEDULED", "engine", step["id"], step["state"], "WAITING"
                    )
                    changed = True
                elif step_type == "ASSESSMENT":
                    # An assessment hangs off a request. There is no third
                    # level, so this node has no meaning anywhere else and
                    # saying so here is cheaper than discovering it later as a
                    # run that never completes.
                    if owner.type != REQUEST:
                        raise ValidationError(
                            f"Step '{step.get('step_key')}' is an ASSESSMENT node, which "
                            "can only appear in a request's workflow")
                    self.repository.update_step(
                        step["id"], {"state": "WAITING", "execution_status": "WAITING",
                                     "started_at": now()}
                    )
                    config = step.get("configuration", {})
                    self.start_assessment(owner.id, {
                        "command_id": f"assessment:{owner.id}:{step['id']}:1",
                        "workflow_version_id": config["assessment_workflow_version_id"],
                        "title": config.get("title", step.get("step_key", "Assessment")),
                        "assessment_type": config.get("assessment_type"),
                        "variables": config.get("variables", {}),
                        "actor": "engine",
                        "parent_step_instance_id": step["id"],
                        "required": config.get("required", True),
                    })
                    changed = True
            for step in self.repository.list_steps_by_execution(owner, "WAITING"):
                if step["step_type"] == "WAIT_SIGNAL":
                    config = step.get("configuration", {})
                    signal = self.repository.unconsumed_signal(
                        owner, config["signal_type"], config.get("correlation_key")
                    )
                    if signal:
                        self.repository.consume_signal(signal["id"], step["id"])
                        self._complete_system_step(
                            owner, step, "SIGNAL_CONSUMED", signal.get("payload", {})
                        )
                        changed = True
                elif step["step_type"] == "AUTOMATED_TASK":
                    job = self.repository.job_for_step(step["id"])
                    if job and job["status"] == "SUCCEEDED":
                        self._complete_system_step(
                            owner, step, "AUTOMATION_SUCCEEDED", job.get("result") or {}
                        )
                        changed = True
                elif step["step_type"] == "ASSESSMENT":
                    assessments = [
                        item for item in self.repository.get_aggregate(owner)["assessments"]
                        if item.get("parent_step_instance_id") == step["id"]
                    ]
                    required = [item for item in assessments if item["required_flag"]]
                    awaited = required or assessments
                    if awaited and all(item["execution_status"] == "COMPLETED" for item in awaited):
                        self._complete_system_step(owner, step, "ASSESSMENTS_COMPLETED")
                        changed = True
                    elif any(item["execution_status"] in {"FAILED", "CANCELLED"} for item in required):
                        policy = step.get("configuration", {}).get(
                            "assessment_failure_policy", "FAIL")
                        if policy == "CONTINUE":
                            # The request carries on once every awaited
                            # assessment has settled, successfully or not.
                            if all(item["execution_status"] in TERMINAL_EXECUTION
                                   for item in awaited):
                                self._complete_system_step(
                                    owner, step, "ASSESSMENTS_SETTLED",
                                    {"assessment_failure_policy": "CONTINUE"})
                                changed = True
                        else:
                            self.repository.update_step(
                                step["id"], {"execution_status": "FAILED", "completed_at": now()}
                            )
                            self.repository.append_event(
                                owner, "ASSESSMENT_FAILED", "engine", step["id"]
                            )
                            self._handle_step_failure(
                                owner, self.repository.get_step(step["id"]))
                            changed = True
            if not changed:
                return
        raise ExecutionError(
            f"{owner.type} {owner.id} did not reach a stable state within "
            f"{DRIVER_ITERATION_LIMIT} driver iterations")

    def _drive_request(self, aggregate: dict[str, Any]) -> None:
        """An assessment settling may be what a request's ASSESSMENT node awaits."""
        if aggregate.get("owner_type") != ASSESSMENT:
            return
        request = Owner(REQUEST, aggregate["request_id"])
        row = self.repository.get_aggregate_row(request)
        if row and row["execution_status"] == "RUNNING":
            before = self._execution_signature(self.repository.get_aggregate(request))
            self._drive(request)
            after = self.repository.get_aggregate(request)
            if before != self._execution_signature(after):
                current = self.repository.get_aggregate_row(request)
                self.repository.update_aggregate(
                    request, {"revision": current["revision"] + 1})

    @staticmethod
    def _execution_signature(aggregate: dict[str, Any] | None) -> Any:
        """State changed by the driver, excluding the optimistic revision itself."""
        if aggregate is None:
            return None
        return (
            aggregate.get("lifecycle_status"), aggregate.get("execution_status"),
            aggregate.get("current_stage"), aggregate.get("completed_at"),
            tuple(
                (step["id"], step.get("state"), step.get("execution_status"),
                 step.get("iteration_number"), step.get("started_at"),
                 step.get("completed_at"), step.get("result"))
                for step in aggregate.get("steps", [])
            ),
        )
