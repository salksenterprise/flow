from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .schema import SCHEMA


def decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in list(result):
        if key.endswith("_json") and result[key] is not None:
            result[key[:-5]] = json.loads(result.pop(key))
    return result


class SQLiteWorkflowRepository:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        with self.transaction():
            self.db.executescript(SCHEMA)

    @property
    def db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Repository operation requires a transaction")
        return self._connection

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._connection is not None:
            yield
            return
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        self._connection = connection
        try:
            yield
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self._connection = None
            connection.close()

    def import_template(self, data: dict[str, Any]) -> dict[str, int]:
        row = self.db.execute("SELECT id FROM workflow_definition WHERE key=?", (data["key"],)).fetchone()
        if row:
            definition_id = row["id"]
            self.db.execute(
                "UPDATE workflow_definition SET name=?, description=?, domain=? WHERE id=?",
                (data["name"], data.get("description", ""), data.get("domain", "GENERIC"), definition_id),
            )
        else:
            definition_id = self.db.execute(
                "INSERT INTO workflow_definition (key,name,description,domain) VALUES (?,?,?,?)",
                (data["key"], data["name"], data.get("description", ""), data.get("domain", "GENERIC")),
            ).lastrowid
        if self.db.execute(
            "SELECT 1 FROM workflow_version WHERE definition_id=? AND version_number=?",
            (definition_id, data.get("version", 1)),
        ).fetchone():
            raise ValueError("That workflow version already exists")
        status = "PUBLISHED" if data.get("publish", True) else "DRAFT"
        version_id = self.db.execute(
            """INSERT INTO workflow_version (definition_id,version_number,status,published_at)
            VALUES (?,?,?,CASE WHEN ?='PUBLISHED' THEN CURRENT_TIMESTAMP END)""",
            (definition_id, data.get("version", 1), status, status),
        ).lastrowid
        step_ids: dict[str, int] = {}
        for step in data["steps"]:
            step_ids[step["key"]] = self.db.execute(
                """INSERT INTO step_definition
                (workflow_version_id,step_key,name,step_type,stage,description,assignment_role,join_rule,configuration_json)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (version_id, step["key"], step["name"], step["type"], step.get("stage", ""),
                 step.get("description", ""), step.get("assignment_role"), step.get("join_rule"),
                 json.dumps(step.get("configuration", {}))),
            ).lastrowid
        for transition in data["transitions"]:
            self.db.execute(
                """INSERT INTO transition_definition
                (workflow_version_id,from_step_id,to_step_id,condition_json,priority) VALUES (?,?,?,?,?)""",
                (version_id, step_ids[transition["from_step"]], step_ids[transition["to_step"]],
                 json.dumps(transition["condition"]) if transition.get("condition") else None,
                 transition.get("priority", 100)),
            )
        return {"definition_id": definition_id, "workflow_version_id": version_id}

    def list_templates(self) -> list[dict[str, Any]]:
        with self.transaction():
            return [decode(row) for row in self.db.execute(
                """SELECT wd.*,wv.id AS workflow_version_id,wv.version_number,wv.status AS version_status,
                (SELECT COUNT(*) FROM step_definition sd WHERE sd.workflow_version_id=wv.id) AS step_count
                FROM workflow_definition wd JOIN workflow_version wv ON wv.definition_id=wd.id
                ORDER BY wd.name,wv.version_number DESC"""
            )]

    def get_template(self, version_id: int) -> dict[str, Any] | None:
        with self.transaction():
            version = decode(self.db.execute(
                """SELECT wv.*,wd.key,wd.name,wd.description,wd.domain FROM workflow_version wv
                JOIN workflow_definition wd ON wd.id=wv.definition_id WHERE wv.id=?""", (version_id,)
            ).fetchone())
            if not version:
                return None
            version["steps"] = [decode(row) for row in self.db.execute(
                "SELECT * FROM step_definition WHERE workflow_version_id=? ORDER BY id", (version_id,)
            )]
            version["transitions"] = [decode(row) for row in self.db.execute(
                """SELECT td.*,a.step_key AS from_step,b.step_key AS to_step FROM transition_definition td
                JOIN step_definition a ON a.id=td.from_step_id JOIN step_definition b ON b.id=td.to_step_id
                WHERE td.workflow_version_id=? ORDER BY td.priority,td.id""", (version_id,)
            )]
            return version

    def get_published_version(self, version_id: int) -> dict[str, Any] | None:
        return decode(self.db.execute(
            "SELECT * FROM workflow_version WHERE id=? AND status='PUBLISHED'", (version_id,)
        ).fetchone())

    def command_result(self, command_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT result_json FROM workflow_command WHERE command_id=?", (command_id,)).fetchone()
        return json.loads(row["result_json"]) if row else None

    def record_command(self, command_id: str, workflow_id: int, action: str, result: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO workflow_command (command_id,workflow_instance_id,action,result_json) VALUES (?,?,?,?)",
            (command_id, workflow_id, action, json.dumps(result)),
        )

    def create_workflow(self, data: dict[str, Any]) -> int:
        return self.db.execute(
            """INSERT INTO workflow_instance
            (workflow_version_id,title,business_type,business_key,correlation_id,variables_json,created_by)
            VALUES (?,?,?,?,?,?,?)""",
            (data["workflow_version_id"], data["title"], data.get("business_type"), data.get("business_key"),
             data.get("correlation_id"), json.dumps(data.get("variables", {})), data.get("actor", "system")),
        ).lastrowid

    def add_subjects(self, workflow_id: int, subjects: list[dict[str, Any]]) -> None:
        for subject in subjects:
            self.db.execute(
                """INSERT INTO workflow_subject
                (workflow_instance_id,subject_type,subject_id,source_system,relationship) VALUES (?,?,?,?,?)""",
                (workflow_id, subject["subject_type"], subject["subject_id"], subject["source_system"],
                 subject.get("relationship", "PRIMARY")),
            )

    def create_step_instances(self, workflow_id: int, version_id: int) -> None:
        for step in self.db.execute("SELECT id FROM step_definition WHERE workflow_version_id=?", (version_id,)):
            self.db.execute(
                "INSERT INTO step_instance (workflow_instance_id,step_definition_id) VALUES (?,?)",
                (workflow_id, step["id"]),
            )

    def get_instance_row(self, workflow_id: int) -> dict[str, Any] | None:
        return decode(self.db.execute("SELECT * FROM workflow_instance WHERE id=?", (workflow_id,)).fetchone())

    def list_workflows(self) -> list[dict[str, Any]]:
        with self.transaction():
            return [decode(row) for row in self.db.execute(
                """SELECT wi.*,wd.name AS workflow_name FROM workflow_instance wi
                JOIN workflow_version wv ON wv.id=wi.workflow_version_id
                JOIN workflow_definition wd ON wd.id=wv.definition_id ORDER BY wi.id DESC"""
            )]

    def get_workflow(self, workflow_id: int) -> dict[str, Any] | None:
        manage_transaction = self._connection is None
        context = self.transaction() if manage_transaction else _null_context()
        with context:
            workflow = decode(self.db.execute(
                """SELECT wi.*,wd.name AS workflow_name,wv.version_number FROM workflow_instance wi
                JOIN workflow_version wv ON wv.id=wi.workflow_version_id
                JOIN workflow_definition wd ON wd.id=wv.definition_id WHERE wi.id=?""", (workflow_id,)
            ).fetchone())
            if not workflow:
                return None
            workflow["subjects"] = [decode(row) for row in self.db.execute(
                "SELECT * FROM workflow_subject WHERE workflow_instance_id=?", (workflow_id,)
            )]
            steps = []
            for row in self.db.execute(
                """SELECT si.*,sd.step_key,sd.name,sd.step_type,sd.stage,sd.description,sd.assignment_role
                FROM step_instance si JOIN step_definition sd ON sd.id=si.step_definition_id
                WHERE si.workflow_instance_id=? ORDER BY sd.id""", (workflow_id,)
            ):
                step = decode(row)
                step["assignments"] = [decode(item) for item in self.db.execute(
                    "SELECT * FROM work_assignment WHERE step_instance_id=?", (row["id"],)
                )]
                steps.append(step)
            workflow["steps"] = steps
            workflow["events"] = [decode(row) for row in self.db.execute(
                "SELECT * FROM workflow_event WHERE workflow_instance_id=? ORDER BY sequence_number DESC", (workflow_id,)
            )]
            return workflow

    def list_not_ready_steps(self, workflow_id: int) -> list[dict[str, Any]]:
        return [decode(row) for row in self.db.execute(
            """SELECT si.*,sd.step_type,sd.stage,sd.assignment_role,sd.join_rule FROM step_instance si
            JOIN step_definition sd ON sd.id=si.step_definition_id
            WHERE si.workflow_instance_id=? AND si.state='NOT_READY'""", (workflow_id,)
        )]

    def list_ready_steps(self, workflow_id: int) -> list[dict[str, Any]]:
        return [decode(row) for row in self.db.execute(
            """SELECT si.*,sd.step_type FROM step_instance si JOIN step_definition sd ON sd.id=si.step_definition_id
            WHERE si.workflow_instance_id=? AND si.state='READY'""", (workflow_id,)
        )]

    def incoming(self, workflow_id: int, step_definition_id: int) -> list[dict[str, Any]]:
        return [decode(row) for row in self.db.execute(
            """SELECT td.condition_json,pred.state,pred.id AS step_instance_id FROM transition_definition td
            JOIN step_instance pred ON pred.step_definition_id=td.from_step_id AND pred.workflow_instance_id=?
            WHERE td.to_step_id=?""", (workflow_id, step_definition_id),
        )]

    def update_step(self, step_id: int, values: dict[str, Any]) -> None:
        data = dict(values)
        if "result" in data:
            data["result_json"] = json.dumps(data.pop("result"))
        assignments = ",".join(f"{key}=?" for key in data)
        self.db.execute(f"UPDATE step_instance SET {assignments} WHERE id=?", (*data.values(), step_id))

    def update_workflow(self, workflow_id: int, values: dict[str, Any]) -> None:
        assignments = ",".join(f"{key}=?" for key in values)
        self.db.execute(f"UPDATE workflow_instance SET {assignments} WHERE id=?", (*values.values(), workflow_id))

    def get_step(self, step_id: int) -> dict[str, Any] | None:
        return decode(self.db.execute(
            """SELECT si.*,sd.step_type FROM step_instance si JOIN step_definition sd
            ON sd.id=si.step_definition_id WHERE si.id=?""", (step_id,)
        ).fetchone())

    def create_assignment(self, step_id: int, assignee_type: str, assignee: str) -> None:
        self.db.execute(
            "INSERT INTO work_assignment (step_instance_id,assignee_type,assignee) VALUES (?,?,?)",
            (step_id, assignee_type, assignee),
        )

    def replace_assignments(self, step_id: int, assignee_type: str, assignee: str) -> None:
        self.db.execute("UPDATE work_assignment SET status='REPLACED' WHERE step_instance_id=? AND status='OPEN'", (step_id,))
        self.create_assignment(step_id, assignee_type, assignee)

    def append_event(self, workflow_id: int, event_type: str, actor: str, step_id: int | None = None, previous: str | None = None, new: str | None = None, payload: dict[str, Any] | None = None) -> None:
        event_id = str(uuid.uuid4())
        sequence = self.db.execute(
            "SELECT COALESCE(MAX(sequence_number),0)+1 FROM workflow_event WHERE workflow_instance_id=?", (workflow_id,)
        ).fetchone()[0]
        workflow = self.db.execute(
            "SELECT business_type,business_key,correlation_id FROM workflow_instance WHERE id=?", (workflow_id,)
        ).fetchone()
        event_payload = {
            "event_id": event_id, "event_type": event_type, "workflow_instance_id": workflow_id,
            "step_instance_id": step_id, "sequence_number": sequence, "actor": actor,
            "previous_state": previous, "new_state": new, "business_type": workflow["business_type"],
            "business_key": workflow["business_key"], "correlation_id": workflow["correlation_id"],
            "payload": payload or {},
        }
        self.db.execute(
            """INSERT INTO workflow_event
            (event_id,workflow_instance_id,sequence_number,step_instance_id,event_type,actor,previous_state,new_state,payload_json)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (event_id, workflow_id, sequence, step_id, event_type, actor, previous, new, json.dumps(payload or {})),
        )
        self.db.execute(
            "INSERT INTO outbox_event (event_id,event_type,payload_json) VALUES (?,?,?)",
            (event_id, event_type, json.dumps(event_payload)),
        )

    def create_subscription(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.transaction():
            subscription_id = self.db.execute(
                """INSERT INTO webhook_subscription (name,target_url,event_types_json,secret)
                VALUES (?,?,?,?)""",
                (data["name"], data["target_url"], json.dumps(data.get("event_types", [])), data.get("secret")),
            ).lastrowid
            return decode(self.db.execute("SELECT * FROM webhook_subscription WHERE id=?", (subscription_id,)).fetchone())

    def list_subscriptions(self, active_only: bool = False) -> list[dict[str, Any]]:
        with self.transaction():
            query = "SELECT * FROM webhook_subscription" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
            return [decode(row) for row in self.db.execute(query)]

    def pending_outbox(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.transaction():
            return [decode(row) for row in self.db.execute(
                "SELECT * FROM outbox_event WHERE status='PENDING' ORDER BY id LIMIT ?", (limit,)
            )]

    def record_delivery(self, outbox_id: int, subscription_id: int, delivered: bool, error: str | None = None) -> None:
        with self.transaction():
            existing = self.db.execute(
                "SELECT id,attempts FROM outbox_delivery WHERE outbox_event_id=? AND subscription_id=?",
                (outbox_id, subscription_id),
            ).fetchone()
            status = "DELIVERED" if delivered else "FAILED"
            if existing:
                self.db.execute(
                    """UPDATE outbox_delivery SET status=?,attempts=?,last_error=?,
                    delivered_at=CASE WHEN ? THEN CURRENT_TIMESTAMP END WHERE id=?""",
                    (status, existing["attempts"] + 1, error, delivered, existing["id"]),
                )
            else:
                self.db.execute(
                    """INSERT INTO outbox_delivery
                    (outbox_event_id,subscription_id,status,attempts,last_error,delivered_at)
                    VALUES (?,?,?,?,?,CASE WHEN ? THEN CURRENT_TIMESTAMP END)""",
                    (outbox_id, subscription_id, status, 1, error, delivered),
                )

    def mark_outbox(self, outbox_id: int, delivered: bool) -> None:
        with self.transaction():
            self.db.execute(
                """UPDATE outbox_event SET status=?,attempts=attempts+1,
                processed_at=CASE WHEN ? THEN CURRENT_TIMESTAMP END WHERE id=?""",
                ("DELIVERED" if delivered else "PENDING", delivered, outbox_id),
            )


@contextmanager
def _null_context():
    yield
