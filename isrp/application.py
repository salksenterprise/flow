"""The ISRP application boundary around the fused orchestration modules.

The application owns database connections and transaction scope. Domain fields
and orchestration state are written on the same aggregate rows and commit or
roll back together.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .orchestration import (
    ASSESSMENT, REQUEST, Actor, ConflictError, NotFoundError, Owner,
    SQLiteWorkflowRepository, ValidationError, WorkflowEngine,
)
from .orchestration.store import utcnow


ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = ROOT / "templates"
REFERENCE = re.compile(r"^[A-Z][A-Z0-9-]{2,39}$")
ASSESSMENT_TYPES = {"APPLICATION", "ARCHITECTURE", "EXTERNAL", "INFRASTRUCTURE"}


def _required_text(data: dict[str, Any], field: str, maximum: int) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string")
    value = value.strip()
    if len(value) > maximum:
        raise ValidationError(f"{field} must be {maximum} characters or fewer")
    return value


def _optional_text(data: dict[str, Any], field: str, maximum: int) -> str:
    value = data.get(field, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    value = value.strip()
    if len(value) > maximum:
        raise ValidationError(f"{field} must be {maximum} characters or fewer")
    return value


class ISRPApplication:
    """Use cases consumed by HTTP, jobs, tests, and future adapters."""

    def __init__(self, database: str | Path):
        self.database = str(database)
        self.repository = SQLiteWorkflowRepository()
        self.engine = WorkflowEngine(self.repository)
        self.request_version_id: int | None = None
        self.assessment_version_id: int | None = None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database, timeout=30.0, isolation_level=None, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        connection.execute("BEGIN IMMEDIATE")
        try:
            with self.repository.using(connection):
                yield connection
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")
        finally:
            connection.close()

    def initialize(self) -> None:
        connection = self._connect()
        try:
            self.repository.create_schema(connection)
        finally:
            connection.close()
        with self.transaction():
            templates = {item["key"]: item for item in self.engine.list_templates()}
            for filename in ("request-intake.json", "assessment-review.json"):
                template = json.loads((TEMPLATE_DIR / filename).read_text(encoding="utf-8"))
                if template["key"] not in templates:
                    self.engine.import_template(template)
            templates = {item["key"]: item for item in self.engine.list_templates()}
            self.request_version_id = templates["isrp-request-intake"]["workflow_version_id"]
            self.assessment_version_id = templates["isrp-assessment-review"]["workflow_version_id"]

    @staticmethod
    def _actor(value: Any) -> Actor:
        actor = Actor.from_value(value or "isrp.user")
        if not actor.actor_id.strip():
            raise ValidationError("actor_id must be a non-empty string")
        return actor

    @staticmethod
    def _decorate_request(request: dict[str, Any]) -> dict[str, Any]:
        request = dict(request)
        request["reference"] = request.get("reference") or f"ISR-{request['id']:06d}"
        request["open_work_count"] = sum(
            step["execution_status"] in {"READY", "ACTIVE", "WAITING"}
            and step["step_type"] not in {"WAIT_SIGNAL", "END"}
            for step in request.get("steps", [])
        )
        return request

    def list_requests(self) -> list[dict[str, Any]]:
        with self.transaction():
            return [self._decorate_request(item) for item in self.engine.list_requests()]

    def get_request(self, request_id: int) -> dict[str, Any]:
        with self.transaction():
            return self._decorate_request(self.engine.get_request(request_id))

    def create_request(self, data: dict[str, Any], actor: Any = None) -> dict[str, Any]:
        title = _required_text(data, "title", 200)
        requester_name = _required_text(data, "requester_name", 120)
        requester_email = _required_text(data, "requester_email", 254)
        if "@" not in requester_email:
            raise ValidationError("requester_email must be a valid email address")
        command_id = _required_text(data, "command_id", 120)
        reference = _optional_text(data, "reference", 40).upper() or None
        if reference and not REFERENCE.fullmatch(reference):
            raise ValidationError(
                "reference must start with a letter and contain only letters, numbers, or dashes")
        resolved = self._actor(actor or data.get("actor"))
        with self.transaction() as connection:
            try:
                request = self.engine.start_request({
                    "command_id": command_id,
                    "workflow_version_id": self.request_version_id,
                    "reference": reference,
                    "title": title,
                    "summary": _optional_text(data, "summary", 4000),
                    "requester_name": requester_name,
                    "requester_email": requester_email,
                    "organization_name": _optional_text(data, "organization_name", 160),
                    "source_system": _optional_text(data, "source_system", 80) or "ISRP",
                    "actor": resolved,
                    "variables": {
                        "identity_review_required": bool(data.get("identity_review_required")),
                        "network_review_required": bool(data.get("network_review_required")),
                    },
                })
                final_reference = reference or f"ISR-{request['id']:06d}"
                connection.execute(
                    "UPDATE isrp_request SET reference=? WHERE id=?",
                    (final_reference, request["id"]),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError("Request reference already exists") from error
            return self._decorate_request(self.engine.get_request(request["id"]))

    def submit_request(self, request_id: int, data: dict[str, Any],
                       actor: Any = None) -> dict[str, Any]:
        command_id = _required_text(data, "command_id", 120)
        resolved = self._actor(actor or data.get("actor"))
        with self.transaction() as connection:
            current = self.engine.get_request(request_id)
            submitted = self.engine.apply_lifecycle_action(Owner(REQUEST, request_id), {
                "command_id": command_id,
                "action": "submit",
                "actor": resolved,
                "expected_revision": data.get("expected_revision", current["revision"]),
            })
            connection.execute(
                "UPDATE isrp_request SET submitted_at=COALESCE(submitted_at,?) WHERE id=?",
                (utcnow(), request_id),
            )
            submitted = self.engine.receive_signal(Owner(REQUEST, request_id), {
                "command_id": f"{command_id}:submission-signal",
                "signal_type": "REQUEST_SUBMITTED",
                "payload": {},
                "actor": resolved,
            })
            return self._decorate_request(self.engine.get_request(request_id))

    def create_assessment(self, request_id: int, data: dict[str, Any],
                          actor: Any = None) -> dict[str, Any]:
        command_id = _required_text(data, "command_id", 120)
        title = _required_text(data, "title", 200)
        assessment_type = _required_text(data, "assessment_type", 40).upper()
        if assessment_type not in ASSESSMENT_TYPES:
            raise ValidationError(
                f"assessment_type must be one of {', '.join(sorted(ASSESSMENT_TYPES))}")
        resolved = self._actor(actor or data.get("actor"))
        with self.transaction():
            request = self.engine.get_request(request_id)
            if request["lifecycle_status"] not in {"SUBMITTED", "IN_REVIEW"}:
                raise ConflictError("A request must be submitted before assessments are created")
            assessment = self.engine.start_assessment(request_id, {
                "command_id": command_id,
                "workflow_version_id": self.assessment_version_id,
                "title": title,
                "assessment_type": assessment_type,
                "required": bool(data.get("required", True)),
                "actor": resolved,
                "variables": {},
                "expected_revision": data.get("expected_revision", request["revision"]),
            })
            request = self.engine.get_request(request_id)
            if request["lifecycle_status"] == "SUBMITTED":
                self.engine.apply_lifecycle_action(Owner(REQUEST, request_id), {
                    "command_id": f"{command_id}:start-review",
                    "action": "start_review",
                    "actor": resolved,
                    "expected_revision": request["revision"],
                })
            return self.engine.get_assessment(assessment["id"])

    def list_work(self, limit: int = 100) -> dict[str, Any]:
        with self.transaction():
            work = self.engine.list_work(limit=limit)
            work["items"] = [
                item for item in work["items"]
                if item["step_type"] not in {"WAIT_SIGNAL", "END"}
            ]
            work["total"] = len(work["items"])
            return work

    def apply_work_action(self, step_id: int, data: dict[str, Any],
                          actor: Any = None) -> dict[str, Any]:
        action = _required_text(data, "action", 40)
        command_id = _required_text(data, "command_id", 120)
        resolved = self._actor(actor or data.get("actor"))
        with self.transaction():
            return self.engine.apply_action(step_id, {
                "command_id": command_id,
                "action": action,
                "actor": resolved,
                "expected_revision": data.get("expected_revision"),
                "payload": data.get("payload") or {},
                "reason": data.get("reason"),
            })

    def dashboard(self) -> dict[str, Any]:
        with self.transaction():
            requests = self.engine.list_requests()
            assessments = self.engine.list_assessments()
            work = self.engine.list_work(limit=1000)
            active_work = [
                item for item in work["items"]
                if item["step_type"] not in {"WAIT_SIGNAL", "END"}
            ]
            return {
                "requests": len(requests),
                "submitted": sum(item["lifecycle_status"] != "DRAFT" for item in requests),
                "assessments": len(assessments),
                "active_work": len(active_work),
            }


def new_command_id() -> str:
    return str(uuid.uuid4())
