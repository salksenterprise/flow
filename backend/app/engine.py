from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .db import as_dict


SYSTEM_TYPES = {"AUTOMATED_TASK", "FORK", "JOIN", "MILESTONE", "END"}
SATISFIED_STATES = {"COMPLETED", "SKIPPED"}

TRANSITIONS = {
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


def event(
    db: sqlite3.Connection,
    workflow_id: int,
    event_type: str,
    actor: str,
    step_id: int | None = None,
    previous: str | None = None,
    new: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    db.execute(
        """INSERT INTO workflow_event
        (workflow_instance_id, step_instance_id, event_type, actor, previous_state, new_state, payload_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (workflow_id, step_id, event_type, actor, previous, new, json.dumps(payload or {})),
    )


def evaluate(rule: dict[str, Any] | None, context: dict[str, Any]) -> bool:
    if not rule:
        return True
    if "all" in rule:
        return all(evaluate(item, context) for item in rule["all"])
    if "any" in rule:
        return any(evaluate(item, context) for item in rule["any"])
    if "not" in rule:
        return not evaluate(rule["not"], context)
    field, op = rule.get("field"), rule.get("op", "eq")
    actual = context.get(field)
    expected = rule.get("value")
    return {
        "eq": lambda: actual == expected,
        "ne": lambda: actual != expected,
        "in": lambda: actual in (expected or []),
        "not_in": lambda: actual not in (expected or []),
        "exists": lambda: (field in context) == bool(expected),
        "truthy": lambda: bool(actual) == (True if expected is None else bool(expected)),
    }.get(op, lambda: False)()


def import_template(db: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    row = db.execute("SELECT id FROM workflow_definition WHERE key = ?", (data["key"],)).fetchone()
    if row:
        definition_id = row["id"]
        db.execute(
            "UPDATE workflow_definition SET name=?, description=?, domain=? WHERE id=?",
            (data["name"], data.get("description", ""), data.get("domain", "GENERIC"), definition_id),
        )
    else:
        definition_id = db.execute(
            "INSERT INTO workflow_definition (key, name, description, domain) VALUES (?, ?, ?, ?)",
            (data["key"], data["name"], data.get("description", ""), data.get("domain", "GENERIC")),
        ).lastrowid
    existing = db.execute(
        "SELECT id FROM workflow_version WHERE definition_id=? AND version_number=?",
        (definition_id, data.get("version", 1)),
    ).fetchone()
    if existing:
        raise ValueError("That workflow version already exists")
    status = "PUBLISHED" if data.get("publish", True) else "DRAFT"
    published_at = now() if status == "PUBLISHED" else None
    version_id = db.execute(
        "INSERT INTO workflow_version (definition_id, version_number, status, published_at) VALUES (?, ?, ?, ?)",
        (definition_id, data.get("version", 1), status, published_at),
    ).lastrowid
    ids: dict[str, int] = {}
    for step in data["steps"]:
        ids[step["key"]] = db.execute(
            """INSERT INTO step_definition
            (workflow_version_id, step_key, name, step_type, stage, description, assignment_role, join_rule, configuration_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                version_id, step["key"], step["name"], step["type"], step.get("stage", ""),
                step.get("description", ""), step.get("assignment_role"), step.get("join_rule"),
                json.dumps(step.get("configuration", {})),
            ),
        ).lastrowid
    for transition in data["transitions"]:
        db.execute(
            """INSERT INTO transition_definition
            (workflow_version_id, from_step_id, to_step_id, condition_json, priority)
            VALUES (?, ?, ?, ?, ?)""",
            (
                version_id, ids[transition["from_step"]], ids[transition["to_step"]],
                json.dumps(transition["condition"]) if transition.get("condition") else None,
                transition.get("priority", 100),
            ),
        )
    return {"definition_id": definition_id, "workflow_version_id": version_id}


def start_workflow(db: sqlite3.Connection, data: dict[str, Any]) -> int:
    version = db.execute(
        "SELECT * FROM workflow_version WHERE id=? AND status='PUBLISHED'", (data["workflow_version_id"],)
    ).fetchone()
    if not version:
        raise ValueError("Published workflow version not found")
    workflow_id = db.execute(
        """INSERT INTO workflow_instance
        (workflow_version_id, title, input_json, created_by) VALUES (?, ?, ?, ?)""",
        (version["id"], data["title"], json.dumps(data.get("input", {})), data.get("created_by", "system")),
    ).lastrowid
    for subject in data.get("subjects", []):
        db.execute(
            """INSERT INTO workflow_subject
            (workflow_instance_id, subject_type, subject_id, source_system, relationship)
            VALUES (?, ?, ?, ?, ?)""",
            (workflow_id, subject["subject_type"], subject["subject_id"], subject["source_system"], subject.get("relationship", "PRIMARY")),
        )
    steps = db.execute("SELECT id FROM step_definition WHERE workflow_version_id=?", (version["id"],)).fetchall()
    for step in steps:
        db.execute(
            "INSERT INTO step_instance (workflow_instance_id, step_definition_id) VALUES (?, ?)",
            (workflow_id, step["id"]),
        )
    event(db, workflow_id, "WORKFLOW_STARTED", data.get("created_by", "system"), payload=data)
    drive(db, workflow_id)
    return workflow_id


def _incoming(db: sqlite3.Connection, workflow_id: int, step_definition_id: int) -> list[sqlite3.Row]:
    return db.execute(
        """SELECT td.condition_json, pred.state, pred.id AS step_instance_id
        FROM transition_definition td
        JOIN step_instance pred ON pred.step_definition_id=td.from_step_id AND pred.workflow_instance_id=?
        WHERE td.to_step_id=?""",
        (workflow_id, step_definition_id),
    ).fetchall()


def _can_activate(db: sqlite3.Connection, workflow: sqlite3.Row, step: sqlite3.Row) -> bool:
    incoming = _incoming(db, workflow["id"], step["step_definition_id"])
    if not incoming:
        return True
    context = json.loads(workflow["input_json"])
    applicable = [row for row in incoming if evaluate(json.loads(row["condition_json"]) if row["condition_json"] else None, context)]
    if not applicable:
        return False
    if step["step_type"] == "JOIN" and step["join_rule"] == "ANY":
        return any(row["state"] in SATISFIED_STATES for row in applicable)
    return all(row["state"] in SATISFIED_STATES for row in applicable)


def drive(db: sqlite3.Connection, workflow_id: int) -> None:
    for _ in range(100):
        changed = False
        workflow = db.execute("SELECT * FROM workflow_instance WHERE id=?", (workflow_id,)).fetchone()
        if not workflow or workflow["status"] != "ACTIVE":
            return
        candidates = db.execute(
            """SELECT si.*, sd.step_type, sd.stage, sd.assignment_role, sd.join_rule
            FROM step_instance si JOIN step_definition sd ON sd.id=si.step_definition_id
            WHERE si.workflow_instance_id=? AND si.state='NOT_READY'""",
            (workflow_id,),
        ).fetchall()
        for step in candidates:
            if not _can_activate(db, workflow, step):
                continue
            activated = now()
            db.execute("UPDATE step_instance SET state='READY', activated_at=? WHERE id=?", (activated, step["id"]))
            event(db, workflow_id, "STEP_ACTIVATED", "engine", step["id"], "NOT_READY", "READY")
            if step["stage"]:
                db.execute("UPDATE workflow_instance SET current_stage=? WHERE id=?", (step["stage"], workflow_id))
            if step["assignment_role"]:
                db.execute(
                    "INSERT INTO work_assignment (step_instance_id, assignee_type, assignee) VALUES (?, 'ROLE', ?)",
                    (step["id"], step["assignment_role"]),
                )
            changed = True
        system_steps = db.execute(
            """SELECT si.*, sd.step_type FROM step_instance si
            JOIN step_definition sd ON sd.id=si.step_definition_id
            WHERE si.workflow_instance_id=? AND si.state='READY'""",
            (workflow_id,),
        ).fetchall()
        for step in system_steps:
            if step["step_type"] not in SYSTEM_TYPES:
                continue
            stamp = now()
            db.execute(
                "UPDATE step_instance SET state='COMPLETED', started_at=?, completed_at=? WHERE id=?",
                (stamp, stamp, step["id"]),
            )
            event(db, workflow_id, "SYSTEM_STEP_COMPLETED", "engine", step["id"], "READY", "COMPLETED")
            if step["step_type"] == "END":
                db.execute(
                    "UPDATE workflow_instance SET status='COMPLETED', current_stage='End', completed_at=? WHERE id=?",
                    (stamp, workflow_id),
                )
                event(db, workflow_id, "WORKFLOW_COMPLETED", "engine", step["id"])
            changed = True
        if not changed:
            return
    raise RuntimeError("Workflow did not reach a stable state")


def apply_action(db: sqlite3.Connection, step_id: int, action: str, actor: str, payload: dict[str, Any], assignee: str | None = None, assignee_type: str = "USER") -> None:
    step = db.execute(
        """SELECT si.*, sd.step_type FROM step_instance si
        JOIN step_definition sd ON sd.id=si.step_definition_id WHERE si.id=?""", (step_id,)
    ).fetchone()
    if not step:
        raise ValueError("Step instance not found")
    current = step["state"]
    target = TRANSITIONS.get(current, {}).get(action)
    if not target:
        raise ValueError(f"Action '{action}' is not allowed from state '{current}'")
    values: dict[str, Any] = {"state": target}
    if action == "start":
        values["started_at"] = now()
    if target in {"COMPLETED", "SKIPPED", "FAILED", "CANCELLED"}:
        values["completed_at"] = now()
        values["result_json"] = json.dumps(payload)
    assignments = ", ".join(f"{column}=?" for column in values)
    db.execute(f"UPDATE step_instance SET {assignments} WHERE id=?", (*values.values(), step_id))
    if action == "assign" and assignee:
        db.execute("UPDATE work_assignment SET status='REPLACED' WHERE step_instance_id=? AND status='OPEN'", (step_id,))
        db.execute(
            "INSERT INTO work_assignment (step_instance_id, assignee_type, assignee) VALUES (?, ?, ?)",
            (step_id, assignee_type, assignee),
        )
    event(db, step["workflow_instance_id"], f"STEP_{action.upper()}", actor, step_id, current, target, payload)
    drive(db, step["workflow_instance_id"])


def get_workflow(db: sqlite3.Connection, workflow_id: int) -> dict[str, Any] | None:
    workflow = as_dict(db.execute(
        """SELECT wi.*, wd.name AS workflow_name, wv.version_number
        FROM workflow_instance wi
        JOIN workflow_version wv ON wv.id=wi.workflow_version_id
        JOIN workflow_definition wd ON wd.id=wv.definition_id WHERE wi.id=?""", (workflow_id,)
    ).fetchone())
    if not workflow:
        return None
    workflow["subjects"] = [as_dict(row) for row in db.execute("SELECT * FROM workflow_subject WHERE workflow_instance_id=?", (workflow_id,))]
    steps = []
    for row in db.execute(
        """SELECT si.*, sd.step_key, sd.name, sd.step_type, sd.stage, sd.description, sd.assignment_role
        FROM step_instance si JOIN step_definition sd ON sd.id=si.step_definition_id
        WHERE si.workflow_instance_id=? ORDER BY sd.id""", (workflow_id,)
    ):
        item = as_dict(row)
        item["assignments"] = [as_dict(x) for x in db.execute("SELECT * FROM work_assignment WHERE step_instance_id=?", (row["id"],))]
        steps.append(item)
    workflow["steps"] = steps
    workflow["events"] = [as_dict(row) for row in db.execute("SELECT * FROM workflow_event WHERE workflow_instance_id=? ORDER BY id DESC", (workflow_id,))]
    return workflow

