from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from workflow_core.actor import Actor
from workflow_core.errors import ConflictError

from .schema import EVENT_SCHEMA_VERSION, INDEXES, SCHEMA, SCHEMA_VERSION


DEFAULT_LIFECYCLE_FSM = {
    "key": "_builtin.workflow-lifecycle", "name": "Default workflow lifecycle", "version": 1,
    "initial_state": "ACTIVE",
    "states": [{"key": "ACTIVE"}, {"key": "COMPLETED", "terminal": True},
               {"key": "CANCELLED", "terminal": True}],
    "transitions": [
        {"action": "complete", "from": "ACTIVE", "to": "COMPLETED"},
        {"action": "cancel", "from": "ACTIVE", "to": "CANCELLED", "reason_required": True},
        {"action": "reopen", "from": "COMPLETED", "to": "ACTIVE", "reason_required": True},
    ],
}
DEFAULT_STEP_FSM = {
    "key": "_builtin.human-step", "name": "Default human step", "version": 2,
    "initial_state": "NOT_READY",
    "states": [
        {"key": "NOT_READY"}, {"key": "READY"}, {"key": "ASSIGNED"},
        {"key": "IN_PROGRESS"}, {"key": "WAITING"}, {"key": "CLARIFICATION_REQUIRED"},
        {"key": "RESPONSE_RECEIVED"}, {"key": "COMPLETED", "terminal": True},
        {"key": "SKIPPED", "terminal": True}, {"key": "FAILED", "terminal": True},
        {"key": "CANCELLED", "terminal": True},
    ],
    "transitions": [
        {"action": "activate", "from": "NOT_READY", "to": "READY"},
        {"action": "assign", "from": "READY", "to": "ASSIGNED"},
        {"action": "claim", "from": "READY", "to": "ASSIGNED"},
        {"action": "reassign", "from": "ASSIGNED", "to": "ASSIGNED", "reason_required": True},
        {"action": "start", "from": "READY", "to": "IN_PROGRESS"},
        {"action": "start", "from": "ASSIGNED", "to": "IN_PROGRESS"},
        {"action": "skip", "from": "READY", "to": "SKIPPED"},
        {"action": "skip", "from": "ASSIGNED", "to": "SKIPPED"},
        {"action": "cancel", "from": "READY", "to": "CANCELLED"},
        {"action": "cancel", "from": "ASSIGNED", "to": "CANCELLED"},
        {"action": "wait", "from": "IN_PROGRESS", "to": "WAITING"},
        {"action": "request_clarification", "from": "IN_PROGRESS", "to": "CLARIFICATION_REQUIRED"},
        {"action": "complete", "from": "IN_PROGRESS", "to": "COMPLETED"},
        {"action": "fail", "from": "IN_PROGRESS", "to": "FAILED"},
        {"action": "cancel", "from": "IN_PROGRESS", "to": "CANCELLED"},
        {"action": "resume", "from": "WAITING", "to": "IN_PROGRESS"},
        {"action": "cancel", "from": "WAITING", "to": "CANCELLED"},
        {"action": "respond", "from": "CLARIFICATION_REQUIRED", "to": "RESPONSE_RECEIVED"},
        {"action": "cancel", "from": "CLARIFICATION_REQUIRED", "to": "CANCELLED"},
        {"action": "resume", "from": "RESPONSE_RECEIVED", "to": "IN_PROGRESS"},
        {"action": "complete", "from": "RESPONSE_RECEIVED", "to": "COMPLETED"},
        {"action": "cancel", "from": "RESPONSE_RECEIVED", "to": "CANCELLED"},
        {"action": "reopen", "from": "COMPLETED", "to": "READY", "reason_required": True},
    ],
}


def utcnow() -> str:
    """One timestamp format, matching the SQL default exactly.

    Mixing isoformat() with SQLite's CURRENT_TIMESTAMP produced two shapes in
    the same column, and due_at comparisons are string comparisons.
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# Every object Flow owns. A host embedding Flow may already have a table called
# workflow_instance, so these names can be namespaced with a prefix.
FLOW_TABLES = (
    "fsm_definition", "fsm_version", "fsm_state_definition", "fsm_transition_definition",
    "workflow_definition", "workflow_version", "step_definition", "transition_definition",
    "workflow_instance", "workflow_subject", "workflow_fact_history", "step_instance",
    "step_attempt", "work_assignment", "work_candidate", "signal_receipt",
    "automation_job", "durable_timer", "workflow_event", "workflow_command",
    "inbox_event", "outbox_event", "webhook_subscription", "outbox_delivery",
    "schema_metadata",
)
# Longest first so that no name is a prefix of another match. A trailing word
# boundary keeps workflow_instance_id from matching workflow_instance.
_FLOW_OBJECT = re.compile(
    r"\b(" + "|".join(sorted(FLOW_TABLES, key=len, reverse=True)) + r"|idx_\w+)\b")


def apply_prefix(sql: str, prefix: str) -> str:
    return sql if not prefix else _FLOW_OBJECT.sub(lambda m: prefix + m.group(0), sql)


class _PrefixedConnection:
    """Rewrites Flow's object names on the way to the database.

    One choke point rather than a template in each of the hundred-odd inline
    statements, and it works for a connection the host owns as readily as one
    Flow opened.
    """

    __slots__ = ("_connection", "_prefix")

    def __init__(self, connection: sqlite3.Connection, prefix: str):
        self._connection = connection
        self._prefix = prefix

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        return self._connection.execute(apply_prefix(sql, self._prefix), parameters)

    def executescript(self, sql: str) -> sqlite3.Cursor:
        return self._connection.executescript(apply_prefix(sql, self._prefix))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


TERMINAL_PROJECTION = {"COMPLETED": "COMPLETED", "CANCELLED": "CANCELLED", "FAILED": "FAILED"}
TERMINAL_STEP_EXECUTION = ("COMPLETED", "SKIPPED", "FAILED", "CANCELLED")


def project_status(execution_status: str) -> str:
    """Compatibility projection of execution status onto the legacy status field."""
    return TERMINAL_PROJECTION.get(execution_status, "ACTIVE")


def redact_subscription(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["has_secret"] = bool(row.pop("secret", None))
    return row


def decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in list(result):
        if key.endswith("_json") and result[key] is not None:
            result[key[:-5]] = json.loads(result.pop(key))
    return result


class SQLiteWorkflowRepository:
    def __init__(self, path: str | Path | None = None, table_prefix: str = ""):
        """Standalone with a path; embedded with none.

        Embedded, the host binds its own connection with using(), and Flow
        never opens, commits, rolls back or closes one. A table_prefix
        namespaces Flow's tables so they cannot collide with the host's.
        """
        self.path = str(path) if path is not None else None
        self.table_prefix = table_prefix
        self._local = threading.local()

    def _wrap(self, connection: sqlite3.Connection) -> Any:
        return connection if not self.table_prefix else _PrefixedConnection(
            connection, self.table_prefix)

    @property
    def _connection(self) -> sqlite3.Connection | None:
        """The connection for the calling thread. SQLite forbids sharing one."""
        return getattr(self._local, "connection", None)

    @_connection.setter
    def _connection(self, value: sqlite3.Connection | None) -> None:
        self._local.connection = value

    def _connect(self) -> sqlite3.Connection:
        if self.path is None:
            raise RuntimeError(
                "This repository is embedded and has no database path. The host "
                "must bind its own connection: `with repository.using(connection):`"
            )
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        """Standalone convenience: open a connection, install the schema, close it.

        An embedding host calls create_schema() from its own migration step
        instead, so that Flow's tables are versioned alongside the host's.
        """
        connection = self._connect()
        try:
            self.create_schema(connection)
        finally:
            connection.close()

    def create_schema(self, connection: sqlite3.Connection) -> None:
        """Install or upgrade Flow's tables on a host-supplied connection.

        Safe to call on every start. The legacy 0.2 migration rewrites state
        derived from column values, so it runs only for a populated database
        that predates version stamping, and never again afterwards.

        This must not run inside an open transaction: schema changes commit
        implicitly in SQLite, which would commit whatever the host had in
        flight alongside them.
        """
        if connection.in_transaction:
            raise RuntimeError(
                "create_schema cannot run inside an open transaction: DDL commits "
                "implicitly and would commit the host's uncommitted work with it"
            )
        wrapped = self._wrap(connection)
        legacy = self._legacy_database(connection)
        wrapped.executescript(SCHEMA)
        with self.using(connection):
            lifecycle_id = self._ensure_fsm(DEFAULT_LIFECYCLE_FSM, "WORKFLOW")
            step_id = self._ensure_fsm(DEFAULT_STEP_FSM, "STEP")
            if self._schema_version() is None:
                if legacy:
                    self._migrate_legacy_schema(lifecycle_id, step_id)
                self._stamp_schema_version()
        connection.commit()
        # Indexes last: on a legacy database some of them reference columns the
        # migration has only just added.
        wrapped.executescript(INDEXES)
        connection.commit()

    @contextmanager
    def using(self, connection: sqlite3.Connection) -> Iterator[None]:
        """Bind a host-owned connection for the duration of the block.

        Flow reads and writes through it but never commits, rolls back or
        closes it. The host decides where the transaction ends, which is what
        lets a domain write and a workflow transition commit as one unit.
        """
        if self._connection is not None:
            raise RuntimeError("A connection is already bound on this thread")
        previous_factory = connection.row_factory
        connection.row_factory = sqlite3.Row
        self._connection = connection
        try:
            yield
        finally:
            self._connection = None
            connection.row_factory = previous_factory

    def _legacy_database(self, connection: sqlite3.Connection) -> bool:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        prefix = self.table_prefix
        return (f"{prefix}workflow_instance" in tables
                and f"{prefix}schema_metadata" not in tables)

    def _schema_version(self) -> int | None:
        row = self.db.execute(
            "SELECT value FROM schema_metadata WHERE key='schema_version'").fetchone()
        return int(row[0]) if row else None

    def _stamp_schema_version(self) -> None:
        self.db.execute(
            """INSERT INTO schema_metadata(key,value) VALUES ('schema_version',?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,
               updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
            (str(SCHEMA_VERSION),),
        )

    def _migrate_legacy_schema(self, lifecycle_fsm_id: int, step_fsm_id: int) -> None:
        """Additive migration for databases created by the 0.2 SQLite adapter."""
        additions = {
            "workflow_version": [
                ("lifecycle_fsm_version_id", "INTEGER REFERENCES fsm_version(id)"),
            ],
            "step_definition": [
                ("step_fsm_version_id", "INTEGER REFERENCES fsm_version(id)"),
            ],
            "workflow_instance": [
                ("lifecycle_state", "TEXT NOT NULL DEFAULT 'ACTIVE'"),
                ("execution_status", "TEXT NOT NULL DEFAULT 'RUNNING'"),
                ("parent_workflow_instance_id", "INTEGER REFERENCES workflow_instance(id)"),
                ("root_workflow_instance_id", "INTEGER REFERENCES workflow_instance(id)"),
                ("parent_step_instance_id", "INTEGER"),
                ("relationship_type", "TEXT"),
                ("relationship_key", "TEXT"),
                ("required_flag", "INTEGER NOT NULL DEFAULT 1"),
                ("suspended_at", "TEXT"),
                ("cancelled_at", "TEXT"),
            ],
            "step_instance": [
                ("execution_status", "TEXT NOT NULL DEFAULT 'NOT_READY'"),
            ],
            "work_assignment": [
                ("organization_id", "TEXT"), ("assigned_by", "TEXT"), ("reason", "TEXT"),
                ("created_at", "TEXT"), ("ended_at", "TEXT"),
            ],
            "fsm_state_definition": [("category", "TEXT")],
            "workflow_event": [("actor_org_id", "TEXT")],
            "outbox_event": [
                ("max_attempts", "INTEGER NOT NULL DEFAULT 10"),
                ("next_attempt_at", "TEXT"), ("claimed_by", "TEXT"),
                ("claimed_at", "TEXT"), ("last_error", "TEXT"),
            ],
        }
        for table, columns in additions.items():
            existing = {row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")}
            for name, ddl in columns:
                if name not in existing:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        self.db.execute(
            "UPDATE workflow_version SET lifecycle_fsm_version_id=? WHERE lifecycle_fsm_version_id IS NULL",
            (lifecycle_fsm_id,),
        )
        self.db.execute(
            "UPDATE step_definition SET step_fsm_version_id=? WHERE step_fsm_version_id IS NULL",
            (step_fsm_id,),
        )
        self.db.execute(
            "UPDATE workflow_instance SET root_workflow_instance_id=id WHERE root_workflow_instance_id IS NULL"
        )
        self.db.execute(
            """UPDATE workflow_instance SET
               execution_status=CASE WHEN status='COMPLETED' THEN 'COMPLETED' ELSE execution_status END,
               lifecycle_state=CASE WHEN status='COMPLETED' THEN 'COMPLETED' ELSE lifecycle_state END"""
        )
        self.db.execute(
            """UPDATE step_instance SET execution_status=CASE
               WHEN state='NOT_READY' THEN 'NOT_READY' WHEN state='READY' THEN 'READY'
               WHEN state IN ('COMPLETED','SKIPPED') THEN state
               WHEN state IN ('FAILED','CANCELLED') THEN state
               WHEN state IN ('WAITING','CLARIFICATION_REQUIRED') THEN 'WAITING'
               ELSE 'ACTIVE' END"""
        )
        self.db.execute(
            "UPDATE outbox_event SET next_attempt_at=COALESCE(next_attempt_at,created_at,strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
        )
        self.db.execute("DROP INDEX IF EXISTS idx_outbox_status")

    @property
    def db(self) -> Any:
        if self._connection is None:
            raise RuntimeError("Repository operation requires a transaction")
        return self._wrap(self._connection)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._connection is not None:
            yield
            return
        connection = self._connect()
        # BEGIN IMMEDIATE takes the write lock up front. Without it two callers
        # that read before writing both hold a shared lock and deadlock on
        # upgrade, which busy_timeout cannot resolve.
        connection.execute("BEGIN IMMEDIATE")
        self._connection = connection
        try:
            yield
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._connection = None
            connection.close()

    def _ensure_fsm(self, spec: dict[str, Any], scope: str) -> int:
        key, version = spec["key"], int(spec.get("version", 1))
        row = self.db.execute("SELECT id FROM fsm_definition WHERE key=?", (key,)).fetchone()
        if row:
            definition_id = row["id"]
        else:
            definition_id = self.db.execute(
                "INSERT INTO fsm_definition(key,name,scope) VALUES (?,?,?)",
                (key, spec.get("name", key), scope),
            ).lastrowid
        row = self.db.execute(
            "SELECT id FROM fsm_version WHERE definition_id=? AND version_number=?",
            (definition_id, version),
        ).fetchone()
        if row:
            return row["id"]
        version_id = self.db.execute(
            """INSERT INTO fsm_version(definition_id,version_number,status,initial_state,published_at)
               VALUES (?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))""",
            (definition_id, version, "PUBLISHED", spec["initial_state"]),
        ).lastrowid
        for state in spec["states"]:
            state = {"key": state} if isinstance(state, str) else state
            self.db.execute(
                """INSERT INTO fsm_state_definition(fsm_version_id,state_key,terminal,category)
                   VALUES (?,?,?,?)""",
                (version_id, state["key"], bool(state.get("terminal")), state.get("category")),
            )
        for transition in spec["transitions"]:
            self.db.execute(
                """INSERT INTO fsm_transition_definition
                   (fsm_version_id,action,from_state,to_state,guard_json,required_permission,reason_required)
                   VALUES (?,?,?,?,?,?,?)""",
                (version_id, transition["action"], transition["from"], transition["to"],
                 json.dumps(transition["guard"]) if transition.get("guard") else None,
                 transition.get("required_permission"), bool(transition.get("reason_required"))),
            )
        return version_id

    def import_template(self, data: dict[str, Any]) -> dict[str, int]:
        lifecycle_id = self._ensure_fsm(
            data.get("lifecycle_fsm") or DEFAULT_LIFECYCLE_FSM, "WORKFLOW"
        )
        row = self.db.execute("SELECT id FROM workflow_definition WHERE key=?", (data["key"],)).fetchone()
        if row:
            definition_id = row["id"]
            self.db.execute(
                "UPDATE workflow_definition SET name=?,description=?,domain=? WHERE id=?",
                (data["name"], data.get("description", ""), data.get("domain", "GENERIC"), definition_id),
            )
        else:
            definition_id = self.db.execute(
                "INSERT INTO workflow_definition(key,name,description,domain) VALUES (?,?,?,?)",
                (data["key"], data["name"], data.get("description", ""), data.get("domain", "GENERIC")),
            ).lastrowid
        version = int(data.get("version", 1))
        if self.db.execute(
            "SELECT 1 FROM workflow_version WHERE definition_id=? AND version_number=?",
            (definition_id, version),
        ).fetchone():
            raise ConflictError(
                f"Version {version} of workflow '{data['key']}' already exists. "
                "Published versions are immutable; publish a new version number.")
        status = "PUBLISHED" if data.get("publish", True) else "DRAFT"
        version_id = self.db.execute(
            """INSERT INTO workflow_version
               (definition_id,version_number,status,lifecycle_fsm_version_id,published_at)
               VALUES (?,?,?,?,CASE WHEN ?='PUBLISHED' THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') END)""",
            (definition_id, version, status, lifecycle_id, status),
        ).lastrowid
        default_step_id = self._ensure_fsm(DEFAULT_STEP_FSM, "STEP")
        step_ids: dict[str, int] = {}
        for step in data["steps"]:
            step_fsm_id = self._ensure_fsm(step["fsm"], "STEP") if step.get("fsm") else default_step_id
            step_ids[step["key"]] = self.db.execute(
                """INSERT INTO step_definition
                   (workflow_version_id,step_key,name,step_type,stage,description,assignment_role,
                    join_rule,step_fsm_version_id,configuration_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (version_id, step["key"], step["name"], step["type"], step.get("stage", ""),
                 step.get("description", ""), step.get("assignment_role"), step.get("join_rule"),
                 step_fsm_id, json.dumps(step.get("configuration", {}))),
            ).lastrowid
        for edge in data["transitions"]:
            self.db.execute(
                """INSERT INTO transition_definition
                   (workflow_version_id,from_step_id,to_step_id,condition_json,priority)
                   VALUES (?,?,?,?,?)""",
                (version_id, step_ids[edge["from_step"]], step_ids[edge["to_step"]],
                 json.dumps(edge["condition"]) if edge.get("condition") else None,
                 edge.get("priority", 100)),
            )
        return {"definition_id": definition_id, "workflow_version_id": version_id}

    def list_templates(self) -> list[dict[str, Any]]:
        with self.transaction():
            return [decode(row) for row in self.db.execute(
                """SELECT wd.*,wv.id AS workflow_version_id,wv.version_number,wv.status AS version_status,
                   (SELECT COUNT(*) FROM step_definition sd WHERE sd.workflow_version_id=wv.id) step_count
                   FROM workflow_definition wd JOIN workflow_version wv ON wv.definition_id=wd.id
                   ORDER BY wd.name,wv.version_number DESC"""
            )]

    def get_template(self, version_id: int) -> dict[str, Any] | None:
        with self.transaction():
            result = decode(self.db.execute(
                """SELECT wv.*,wd.key,wd.name,wd.description,wd.domain FROM workflow_version wv
                   JOIN workflow_definition wd ON wd.id=wv.definition_id WHERE wv.id=?""",
                (version_id,),
            ).fetchone())
            if not result:
                return None
            result["steps"] = [decode(row) for row in self.db.execute(
                "SELECT * FROM step_definition WHERE workflow_version_id=? ORDER BY id", (version_id,)
            )]
            result["transitions"] = [decode(row) for row in self.db.execute(
                """SELECT td.*,a.step_key from_step,b.step_key to_step FROM transition_definition td
                   JOIN step_definition a ON a.id=td.from_step_id
                   JOIN step_definition b ON b.id=td.to_step_id
                   WHERE td.workflow_version_id=? ORDER BY td.priority,td.id""", (version_id,)
            )]
            return result

    def get_published_version(self, version_id: int) -> dict[str, Any] | None:
        return decode(self.db.execute(
            "SELECT * FROM workflow_version WHERE id=? AND status='PUBLISHED'", (version_id,)
        ).fetchone())

    def fsm_initial_state(self, fsm_version_id: int) -> str:
        return self.db.execute("SELECT initial_state FROM fsm_version WHERE id=?", (fsm_version_id,)).fetchone()[0]

    def find_fsm_transition(self, fsm_version_id: int, state: str, action: str) -> dict[str, Any] | None:
        return decode(self.db.execute(
            """SELECT * FROM fsm_transition_definition
               WHERE fsm_version_id=? AND from_state=? AND action=?""",
            (fsm_version_id, state, action),
        ).fetchone())

    def state_is_terminal(self, fsm_version_id: int, state: str) -> bool:
        return bool(self.state_meta(fsm_version_id, state)["terminal"])

    def state_meta(self, fsm_version_id: int, state: str) -> dict[str, Any]:
        """Whether a state is terminal, and the execution category it declares.

        A null category means the definition did not declare one; the engine
        resolves it. Storing the declaration rather than a guess is what stops
        execution status being inferred from state names.
        """
        row = self.db.execute(
            """SELECT terminal,category FROM fsm_state_definition
               WHERE fsm_version_id=? AND state_key=?""",
            (fsm_version_id, state),
        ).fetchone()
        if row is None:
            return {"terminal": False, "category": None}
        return {"terminal": bool(row["terminal"]), "category": row["category"]}

    def command_result(self, command_id: str) -> dict[str, Any] | None:
        """The receipt for a command already applied, or None.

        A receipt records which workflow the command hit and the revision it
        produced. It deliberately does not hold a copy of the workflow: storing
        a full snapshot per command made command storage grow with the square
        of the number of commands.
        """
        row = self.db.execute(
            "SELECT result_json FROM workflow_command WHERE command_id=?", (command_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def record_command(self, command_id: str, workflow_id: int, action: str,
                       revision: int | None = None) -> None:
        receipt = {"workflow_instance_id": workflow_id, "action": action, "revision": revision}
        self.db.execute(
            "INSERT INTO workflow_command(command_id,workflow_instance_id,action,result_json) VALUES (?,?,?,?)",
            (command_id, workflow_id, action, json.dumps(receipt)),
        )

    def create_workflow(self, data: dict[str, Any], version: dict[str, Any]) -> int:
        lifecycle = self.fsm_initial_state(version["lifecycle_fsm_version_id"])
        parent_id = data.get("parent_workflow_instance_id")
        root_id = data.get("root_workflow_instance_id")
        workflow_id = self.db.execute(
            """INSERT INTO workflow_instance
               (workflow_version_id,title,business_type,business_key,correlation_id,lifecycle_state,
                execution_status,variables_json,parent_workflow_instance_id,root_workflow_instance_id,
                parent_step_instance_id,relationship_type,relationship_key,required_flag,created_by)
               VALUES (?,?,?,?,?,?,'RUNNING',?,?,?,?,?,?,?,?)""",
            (data["workflow_version_id"], data["title"], data.get("business_type"), data.get("business_key"),
             data.get("correlation_id"), lifecycle, json.dumps(data.get("variables", {})), parent_id,
             root_id, data.get("parent_step_instance_id"), data.get("relationship_type"),
             data.get("relationship_key"), bool(data.get("required", True)),
             Actor.from_value(data.get("actor", "system")).actor_id),
        ).lastrowid
        if root_id is None:
            resolved_root = workflow_id
            if parent_id:
                parent = self.db.execute(
                    "SELECT root_workflow_instance_id FROM workflow_instance WHERE id=?", (parent_id,)
                ).fetchone()
                resolved_root = parent[0] if parent else workflow_id
            self.db.execute(
                "UPDATE workflow_instance SET root_workflow_instance_id=? WHERE id=?",
                (resolved_root, workflow_id),
            )
        return workflow_id

    def add_subjects(self, workflow_id: int, subjects: list[dict[str, Any]]) -> None:
        for subject in subjects:
            self.db.execute(
                """INSERT INTO workflow_subject
                   (workflow_instance_id,subject_type,subject_id,source_system,relationship)
                   VALUES (?,?,?,?,?)""",
                (workflow_id, subject["subject_type"], subject["subject_id"], subject["source_system"],
                 subject.get("relationship", "PRIMARY")),
            )

    def create_step_instances(self, workflow_id: int, version_id: int) -> None:
        for row in self.db.execute(
            "SELECT id,step_fsm_version_id FROM step_definition WHERE workflow_version_id=?", (version_id,)
        ):
            initial = self.fsm_initial_state(row["step_fsm_version_id"])
            self.db.execute(
                """INSERT INTO step_instance
                   (workflow_instance_id,step_definition_id,state,execution_status)
                   VALUES (?,?,?,'NOT_READY')""", (workflow_id, row["id"], initial),
            )

    def fsm_has_state(self, fsm_version_id: int, state: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM fsm_state_definition WHERE fsm_version_id=? AND state_key=?",
            (fsm_version_id, state),
        ).fetchone() is not None

    def remap_step_instances(self, workflow_id: int, target_version_id: int) -> dict[str, int]:
        """Repoint a workflow's nodes at another version's definitions, by step key.

        Step key is the identity that survives a version change: node ids do
        not, and node order certainly does not. A node whose key exists in both
        versions keeps its state; one that has been removed is retired; one
        that is new starts unready.
        """
        current = {row["step_key"]: dict(row) for row in self.db.execute(
            """SELECT si.id,si.execution_status,sd.step_key
               FROM step_instance si JOIN step_definition sd ON sd.id=si.step_definition_id
               WHERE si.workflow_instance_id=?""", (workflow_id,))}
        target = {row["step_key"]: dict(row) for row in self.db.execute(
            """SELECT id,step_key,step_fsm_version_id FROM step_definition
               WHERE workflow_version_id=?""", (target_version_id,))}

        summary = {"kept": 0, "added": 0, "retired": 0}
        placeholders = ",".join("?" * len(TERMINAL_STEP_EXECUTION))
        for key, row in current.items():
            if key in target:
                self.db.execute(
                    "UPDATE step_instance SET step_definition_id=? WHERE id=?",
                    (target[key]["id"], row["id"]))
                summary["kept"] += 1
            else:
                self.db.execute(
                    f"""UPDATE step_instance SET execution_status='CANCELLED',completed_at=?
                        WHERE id=? AND execution_status NOT IN ({placeholders})""",
                    (utcnow(), row["id"], *TERMINAL_STEP_EXECUTION))
                summary["retired"] += 1
        for key, row in target.items():
            if key not in current:
                self.db.execute(
                    """INSERT INTO step_instance
                       (workflow_instance_id,step_definition_id,state,execution_status)
                       VALUES (?,?,?,'NOT_READY')""",
                    (workflow_id, row["id"], self.fsm_initial_state(row["step_fsm_version_id"])))
                summary["added"] += 1
        return summary

    def get_instance_row(self, workflow_id: int) -> dict[str, Any] | None:
        return decode(self.db.execute("SELECT * FROM workflow_instance WHERE id=?", (workflow_id,)).fetchone())

    def list_workflows(self) -> list[dict[str, Any]]:
        with self.transaction():
            items = [decode(row) for row in self.db.execute(
                """SELECT wi.*,wd.name workflow_name FROM workflow_instance wi
                   JOIN workflow_version wv ON wv.id=wi.workflow_version_id
                   JOIN workflow_definition wd ON wd.id=wv.definition_id ORDER BY wi.id DESC"""
            )]
            for item in items:
                item["status"] = project_status(item["execution_status"])
            return items

    def get_workflow(self, workflow_id: int) -> dict[str, Any] | None:
        context = self.transaction() if self._connection is None else _null_context()
        with context:
            workflow = decode(self.db.execute(
                """SELECT wi.*,wd.name workflow_name,wv.version_number,wv.lifecycle_fsm_version_id
                   FROM workflow_instance wi JOIN workflow_version wv ON wv.id=wi.workflow_version_id
                   JOIN workflow_definition wd ON wd.id=wv.definition_id WHERE wi.id=?""",
                (workflow_id,),
            ).fetchone())
            if not workflow:
                return None
            workflow["status"] = project_status(workflow["execution_status"])
            workflow["subjects"] = [decode(row) for row in self.db.execute(
                "SELECT * FROM workflow_subject WHERE workflow_instance_id=?", (workflow_id,)
            )]
            workflow["children"] = [decode(row) for row in self.db.execute(
                """SELECT id,title,business_type,business_key,lifecycle_state,execution_status,
                   parent_step_instance_id,relationship_type,relationship_key,required_flag
                   FROM workflow_instance WHERE parent_workflow_instance_id=? ORDER BY id""", (workflow_id,)
            )]
            workflow["steps"] = []
            for row in self.db.execute(
                """SELECT si.*,sd.step_key,sd.name,sd.step_type,sd.stage,sd.description,
                   sd.assignment_role,sd.join_rule,sd.step_fsm_version_id,sd.configuration_json
                   FROM step_instance si JOIN step_definition sd ON sd.id=si.step_definition_id
                   WHERE si.workflow_instance_id=? ORDER BY sd.id""", (workflow_id,)
            ):
                step = decode(row)
                step["assignments"] = [decode(item) for item in self.db.execute(
                    "SELECT * FROM work_assignment WHERE step_instance_id=? ORDER BY id", (row["id"],)
                )]
                step["candidates"] = [decode(item) for item in self.db.execute(
                    "SELECT * FROM work_candidate WHERE step_instance_id=? ORDER BY id", (row["id"],)
                )]
                workflow["steps"].append(step)
            workflow["events"] = [decode(row) for row in self.db.execute(
                "SELECT * FROM workflow_event WHERE workflow_instance_id=? ORDER BY sequence_number DESC",
                (workflow_id,),
            )]
            return workflow

    def get_step(self, step_id: int) -> dict[str, Any] | None:
        return decode(self.db.execute(
            """SELECT si.*,sd.step_key,sd.step_type,sd.stage,sd.assignment_role,sd.join_rule,
               sd.step_fsm_version_id,sd.configuration_json
               FROM step_instance si JOIN step_definition sd ON sd.id=si.step_definition_id
               WHERE si.id=?""", (step_id,),
        ).fetchone())

    def list_steps_by_execution(self, workflow_id: int, status: str) -> list[dict[str, Any]]:
        return [decode(row) for row in self.db.execute(
            """SELECT si.*,sd.step_key,sd.step_type,sd.stage,sd.assignment_role,sd.join_rule,
               sd.step_fsm_version_id,sd.configuration_json
               FROM step_instance si JOIN step_definition sd ON sd.id=si.step_definition_id
               WHERE si.workflow_instance_id=? AND si.execution_status=?""", (workflow_id, status),
        )]

    def incoming(self, workflow_id: int, step_definition_id: int) -> list[dict[str, Any]]:
        return [decode(row) for row in self.db.execute(
            """SELECT td.condition_json,pred.execution_status,pred.state,
                      pred.id step_instance_id,sd.configuration_json
               FROM transition_definition td
               JOIN step_instance pred
                 ON pred.step_definition_id=td.from_step_id AND pred.workflow_instance_id=?
               JOIN step_definition sd ON sd.id=td.from_step_id
               WHERE td.to_step_id=?""", (workflow_id, step_definition_id),
        )]

    def update_step(self, step_id: int, values: dict[str, Any]) -> None:
        values = dict(values)
        if "result" in values:
            values["result_json"] = json.dumps(values.pop("result"))
        sql = ",".join(f"{key}=?" for key in values)
        self.db.execute(f"UPDATE step_instance SET {sql} WHERE id=?", (*values.values(), step_id))

    def update_workflow(self, workflow_id: int, values: dict[str, Any]) -> None:
        values = dict(values)
        if "variables" in values:
            values["variables_json"] = json.dumps(values.pop("variables"))
        sql = ",".join(f"{key}=?" for key in values)
        self.db.execute(f"UPDATE workflow_instance SET {sql} WHERE id=?", (*values.values(), workflow_id))

    def set_facts(self, workflow: dict[str, Any], facts: dict[str, Any], actor: str,
                  source_type: str, source_reference: str | None) -> None:
        current = dict(workflow["variables"])
        revision = workflow["revision"] + 1
        for key, value in facts.items():
            previous = current.get(key)
            if previous == value:
                continue
            self.db.execute(
                """INSERT INTO workflow_fact_history
                   (workflow_instance_id,fact_key,previous_value_json,new_value_json,
                    source_type,source_reference,actor,revision) VALUES (?,?,?,?,?,?,?,?)""",
                (workflow["id"], key, json.dumps(previous), json.dumps(value),
                 source_type, source_reference, actor, revision),
            )
            current[key] = value
        self.update_workflow(workflow["id"], {"variables": current, "revision": revision})

    def create_candidates(self, step_id: int, candidates: list[dict[str, Any]]) -> None:
        for item in candidates:
            self.db.execute(
                """INSERT OR IGNORE INTO work_candidate
                   (step_instance_id,candidate_type,candidate_value,organization_id) VALUES (?,?,?,?)""",
                (step_id, item["type"], item["value"], item.get("organization_id")),
            )

    def candidate_allowed(self, step_id: int, actor: dict[str, Any]) -> bool:
        candidates = [dict(row) for row in self.db.execute(
            "SELECT * FROM work_candidate WHERE step_instance_id=?", (step_id,)
        )]
        if not candidates:
            return True
        actor_id = actor.get("actor_id")
        organization_id = actor.get("organization_id")
        memberships = set(actor.get("roles", [])) | set(actor.get("groups", []))
        for item in candidates:
            if item.get("organization_id") and item["organization_id"] != organization_id:
                continue
            if item["candidate_type"] == "USER" and item["candidate_value"] == actor_id:
                return True
            if item["candidate_type"] in {"ROLE", "GROUP"} and item["candidate_value"] in memberships:
                return True
            if item["candidate_type"] == "ORGANIZATION" and item["candidate_value"] == organization_id:
                return True
        return False

    def create_assignment(self, step_id: int, assignee_type: str, assignee: str,
                          organization_id: str | None = None, assigned_by: str | None = None,
                          reason: str | None = None, due_at: str | None = None) -> None:
        self.db.execute(
            """INSERT INTO work_assignment
               (step_instance_id,assignee_type,assignee,organization_id,assigned_by,reason,due_at)
               VALUES (?,?,?,?,?,?,?)""",
            (step_id, assignee_type, assignee, organization_id, assigned_by, reason, due_at),
        )

    def replace_assignments(self, step_id: int, assignee_type: str, assignee: str,
                            organization_id: str | None = None, assigned_by: str | None = None,
                            reason: str | None = None) -> None:
        self.db.execute(
            """UPDATE work_assignment SET status='REPLACED',ended_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
               WHERE step_instance_id=? AND status='OPEN'""", (step_id,),
        )
        self.create_assignment(step_id, assignee_type, assignee, organization_id, assigned_by, reason)

    def record_attempt_start(self, step_id: int, iteration: int) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO step_attempt(step_instance_id,iteration_number) VALUES (?,?)",
            (step_id, iteration),
        )

    def record_attempt_end(self, step_id: int, iteration: int, outcome: str, result: dict[str, Any]) -> None:
        self.db.execute(
            """UPDATE step_attempt SET completed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),outcome=?,result_json=?
               WHERE step_instance_id=? AND iteration_number=?""",
            (outcome, json.dumps(result), step_id, iteration),
        )

    def insert_signal(self, command: dict[str, Any], workflow_id: int) -> None:
        self.db.execute(
            """INSERT INTO signal_receipt
               (command_id,workflow_instance_id,signal_type,correlation_key,payload_json)
               VALUES (?,?,?,?,?)""",
            (command["command_id"], workflow_id, command["signal_type"],
             command.get("correlation_key"), json.dumps(command.get("payload", {}))),
        )

    def inbox_event(self, connector_name: str, provider_event_id: str) -> dict[str, Any] | None:
        return decode(self.db.execute(
            "SELECT * FROM inbox_event WHERE connector_name=? AND provider_event_id=?",
            (connector_name, provider_event_id),
        ).fetchone())

    def insert_inbox_event(self, data: dict[str, Any]) -> int:
        return self.db.execute(
            """INSERT INTO inbox_event
               (connector_name,provider_event_id,event_type,correlation_key,payload_json)
               VALUES (?,?,?,?,?)""",
            (data["connector_name"], data["provider_event_id"], data["event_type"],
             data.get("correlation_key"), json.dumps(data.get("payload", {}))),
        ).lastrowid

    def mark_inbox_processed(self, inbox_id: int, error: str | None = None) -> None:
        self.db.execute(
            """UPDATE inbox_event SET status=?,attempts=attempts+1,
               processed_at=CASE WHEN ? IS NULL THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE processed_at END,
               last_error=? WHERE id=?""",
            ("PROCESSED" if error is None else "FAILED", error, error, inbox_id),
        )

    def unconsumed_signal(self, workflow_id: int, signal_type: str,
                          correlation_key: str | None) -> dict[str, Any] | None:
        return decode(self.db.execute(
            """SELECT * FROM signal_receipt WHERE workflow_instance_id=? AND signal_type=?
               AND (? IS NULL OR correlation_key=?) AND consumed_at IS NULL ORDER BY id LIMIT 1""",
            (workflow_id, signal_type, correlation_key, correlation_key),
        ).fetchone())

    def consume_signal(self, signal_id: int, step_id: int) -> None:
        self.db.execute(
            """UPDATE signal_receipt SET consumed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),consumed_step_instance_id=?
               WHERE id=? AND consumed_at IS NULL""", (step_id, signal_id),
        )

    def create_automation_job(self, workflow_id: int, step: dict[str, Any]) -> None:
        config = step.get("configuration", {})
        self.db.execute(
            """INSERT OR IGNORE INTO automation_job
               (job_key,workflow_instance_id,step_instance_id,handler,max_attempts,input_json)
               VALUES (?,?,?,?,?,?)""",
            (f"{workflow_id}:{step['id']}:{step['iteration_number']}", workflow_id, step["id"],
             config.get("handler", "default"), int(config.get("max_attempts", 3)),
             json.dumps(config.get("input", {}))),
        )

    def claim_jobs(self, worker_id: str, limit: int = 10, lease_seconds: int = 60) -> list[dict[str, Any]]:
        now, lease = utcnow(), (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
        rows = list(self.db.execute(
            """SELECT j.id FROM automation_job j
               JOIN workflow_instance w ON w.id=j.workflow_instance_id
               WHERE w.execution_status='RUNNING' AND (
                   (j.status IN ('QUEUED','RETRY_WAIT') AND j.available_at<=?)
                   OR (j.status='RUNNING' AND j.lease_expires_at<?))
               ORDER BY j.id LIMIT ?""",
            (now, now, limit),
        ))
        result = []
        for row in rows:
            # The WHERE clause repeats the claim condition so that a competing
            # worker that won the race leaves rowcount at zero here.
            claimed = self.db.execute(
                """UPDATE automation_job SET status='RUNNING',claimed_by=?,claimed_at=?,
                   lease_expires_at=?,attempt_count=attempt_count+1
                   WHERE id=? AND (
                       (status IN ('QUEUED','RETRY_WAIT') AND available_at<=?)
                       OR (status='RUNNING' AND lease_expires_at<?))""",
                (worker_id, now, lease, row["id"], now, now),
            )
            if claimed.rowcount != 1:
                continue
            result.append(decode(self.db.execute("SELECT * FROM automation_job WHERE id=?", (row["id"],)).fetchone()))
        return result

    def job_for_step(self, step_id: int) -> dict[str, Any] | None:
        return decode(self.db.execute(
            "SELECT * FROM automation_job WHERE step_instance_id=? ORDER BY id DESC LIMIT 1",
            (step_id,),
        ).fetchone())

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        return decode(self.db.execute("SELECT * FROM automation_job WHERE id=?", (job_id,)).fetchone())

    def update_job(self, job_id: int, values: dict[str, Any]) -> None:
        values = dict(values)
        if "result" in values:
            values["result_json"] = json.dumps(values.pop("result"))
        sql = ",".join(f"{key}=?" for key in values)
        self.db.execute(f"UPDATE automation_job SET {sql} WHERE id=?", (*values.values(), job_id))

    def create_timer(self, workflow_id: int, step: dict[str, Any], due_at: str,
                     action: str = "COMPLETE_STEP") -> None:
        """A durable timer for a node.

        The due time is computed by the engine, which owns the business
        calendar. The key includes the action so that a node can carry both a
        delay timer and a service-level timer for the same iteration.
        """
        config = step.get("configuration", {})
        self.db.execute(
            """INSERT OR IGNORE INTO durable_timer
               (timer_key,workflow_instance_id,step_instance_id,timer_type,action,due_at,payload_json)
               VALUES (?,?,?,'RELATIVE',?,?,?)""",
            (f"{workflow_id}:{step['id']}:{step['iteration_number']}:{action}",
             workflow_id, step["id"], action, due_at,
             json.dumps(config.get("payload", {}))),
        )

    def due_timers(self, limit: int = 50) -> list[dict[str, Any]]:
        """Due timers for running workflows only.

        A suspended workflow leaves its timers scheduled so that they fire once
        it resumes, rather than advancing a workflow that is meant to be idle.
        """
        return [decode(row) for row in self.db.execute(
            """SELECT t.* FROM durable_timer t
               JOIN workflow_instance w ON w.id=t.workflow_instance_id
               WHERE t.status='SCHEDULED' AND t.due_at<=? AND w.execution_status='RUNNING'
               ORDER BY t.due_at,t.id LIMIT ?""", (utcnow(), limit),
        )]

    def cancel_open_work(self, workflow_id: int) -> None:
        """Cancel every unfinished node, timer, job and assignment of a workflow.

        Execution status is the engine-controlled dimension, so it is the one
        set here. The FSM-controlled node state is left untouched, because a
        custom step FSM need not declare a cancelled state.
        """
        stamp = utcnow()
        placeholders = ",".join("?" * len(TERMINAL_STEP_EXECUTION))
        self.db.execute(
            f"""UPDATE step_instance SET execution_status='CANCELLED',completed_at=?
                WHERE workflow_instance_id=?
                AND execution_status NOT IN ({placeholders})""",
            (stamp, workflow_id, *TERMINAL_STEP_EXECUTION),
        )
        self.db.execute(
            """UPDATE durable_timer SET status='CANCELLED',cancelled_at=?
               WHERE workflow_instance_id=? AND status='SCHEDULED'""",
            (stamp, workflow_id),
        )
        self.db.execute(
            """UPDATE automation_job SET status='CANCELLED',completed_at=?,lease_expires_at=NULL
               WHERE workflow_instance_id=? AND status IN ('QUEUED','RETRY_WAIT','RUNNING')""",
            (stamp, workflow_id),
        )
        self.db.execute(
            """UPDATE work_assignment SET status='CANCELLED',ended_at=?
               WHERE status='OPEN' AND step_instance_id IN
               (SELECT id FROM step_instance WHERE workflow_instance_id=?)""",
            (stamp, workflow_id),
        )

    def running_child_ids(self, workflow_id: int) -> list[int]:
        return [row[0] for row in self.db.execute(
            """SELECT id FROM workflow_instance
               WHERE parent_workflow_instance_id=? AND execution_status='RUNNING'
               ORDER BY id""", (workflow_id,),
        )]

    def fire_timer(self, timer_id: int) -> None:
        self.db.execute(
            "UPDATE durable_timer SET status='FIRED',fired_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=? AND status='SCHEDULED'",
            (timer_id,),
        )

    def append_event(self, workflow_id: int, event_type: str, actor: str,
                     step_id: int | None = None, previous: str | None = None,
                     new: str | None = None, payload: dict[str, Any] | None = None,
                     actor_org_id: str | None = None) -> None:
        event_id = str(uuid.uuid4())
        sequence = self.db.execute(
            "SELECT COALESCE(MAX(sequence_number),0)+1 FROM workflow_event WHERE workflow_instance_id=?",
            (workflow_id,),
        ).fetchone()[0]
        workflow = self.db.execute(
            "SELECT business_type,business_key,correlation_id FROM workflow_instance WHERE id=?",
            (workflow_id,),
        ).fetchone()
        event_payload = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event_id": event_id, "event_type": event_type, "workflow_instance_id": workflow_id,
            "step_instance_id": step_id, "sequence_number": sequence, "actor": actor,
            "actor_org_id": actor_org_id, "previous_state": previous, "new_state": new,
            "business_type": workflow["business_type"], "business_key": workflow["business_key"],
            "correlation_id": workflow["correlation_id"], "payload": payload or {},
        }
        self.db.execute(
            """INSERT INTO workflow_event
               (event_id,workflow_instance_id,sequence_number,step_instance_id,event_type,actor,
                actor_org_id,previous_state,new_state,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (event_id, workflow_id, sequence, step_id, event_type, actor, actor_org_id,
             previous, new, json.dumps(payload or {})),
        )
        self.db.execute(
            """INSERT INTO outbox_event(event_id,event_type,payload_json,next_attempt_at)
               VALUES (?,?,?,?)""",
            (event_id, event_type, json.dumps(event_payload), utcnow()),
        )

    def list_work(self, assignee: str | None = None, actor: dict[str, Any] | None = None,
                  execution_status: str | None = None, step_type: str | None = None,
                  business_type: str | None = None, business_key: str | None = None,
                  workflow_id: int | None = None,
                  limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Open work, filtered and paginated.

        Without this a client had to fetch whole workflows and sift them, which
        is why the console could not show anyone their own queue.

        `assignee` matches an open assignment. `actor` matches the candidate
        rules instead, answering 'what could this person claim' rather than
        'what is already theirs'.
        """
        clauses = ["si.execution_status IN ('READY','ACTIVE','WAITING')"]
        args: list[Any] = []
        if assignee:
            clauses.append("""EXISTS (SELECT 1 FROM work_assignment a
                WHERE a.step_instance_id=si.id AND a.status='OPEN' AND a.assignee=?)""")
            args.append(assignee)
        if actor:
            memberships = sorted(set(actor.get("roles") or ()) | set(actor.get("groups") or ()))
            placeholders = ",".join("?" * len(memberships)) or "NULL"
            clauses.append(f"""EXISTS (SELECT 1 FROM work_candidate c
                WHERE c.step_instance_id=si.id
                  AND (c.organization_id IS NULL OR c.organization_id=?)
                  AND ((c.candidate_type='USER' AND c.candidate_value=?)
                       OR (c.candidate_type IN ('ROLE','GROUP') AND c.candidate_value IN ({placeholders}))
                       OR (c.candidate_type='ORGANIZATION' AND c.candidate_value=?)))""")
            args.extend([actor.get("organization_id"), actor.get("actor_id"),
                         *memberships, actor.get("organization_id")])
        for column, value in (("si.execution_status", execution_status),
                              ("sd.step_type", step_type),
                              ("w.business_type", business_type),
                              ("w.business_key", business_key),
                              ("w.id", workflow_id)):
            if value is not None:
                clauses.append(f"{column}=?")
                args.append(value)
        where = " AND ".join(clauses)
        source = """FROM step_instance si
                    JOIN step_definition sd ON sd.id=si.step_definition_id
                    JOIN workflow_instance w ON w.id=si.workflow_instance_id"""
        with self.transaction():
            total = int(self.db.execute(
                f"SELECT COUNT(*) {source} WHERE {where}", args).fetchone()[0])
            rows = [decode(row) for row in self.db.execute(
                f"""SELECT si.id step_instance_id,si.state,si.execution_status,
                           si.iteration_number,si.activated_at,si.started_at,
                           sd.step_key,sd.name,sd.step_type,sd.stage,sd.assignment_role,
                           w.id workflow_instance_id,w.title,w.business_type,
                           w.business_key,w.correlation_id,w.lifecycle_state
                    {source} WHERE {where}
                    ORDER BY si.activated_at,si.id LIMIT ? OFFSET ?""",
                (*args, limit, offset))]
            for row in rows:
                row["assignments"] = [decode(item) for item in self.db.execute(
                    """SELECT assignee_type,assignee,organization_id,status
                       FROM work_assignment WHERE step_instance_id=? AND status='OPEN'""",
                    (row["step_instance_id"],))]
        return {"items": rows, "total": total, "limit": limit, "offset": offset}

    def stuck_workflows(self, limit: int = 50) -> list[dict[str, Any]]:
        """Running workflows with nothing that can advance them.

        No node ready, active or waiting; no job queued or running; no timer
        scheduled. Such a workflow is not slow, it is wedged, and nothing else
        in the system will say so.
        """
        with self.transaction():
            return [decode(row) for row in self.db.execute(
                """SELECT w.id,w.title,w.business_type,w.business_key,w.correlation_id,
                          w.current_stage,w.lifecycle_state,w.created_at
                   FROM workflow_instance w
                   WHERE w.execution_status='RUNNING'
                     AND NOT EXISTS (SELECT 1 FROM step_instance s
                         WHERE s.workflow_instance_id=w.id
                           AND s.execution_status IN ('READY','ACTIVE','WAITING'))
                     AND NOT EXISTS (SELECT 1 FROM automation_job j
                         WHERE j.workflow_instance_id=w.id
                           AND j.status IN ('QUEUED','RETRY_WAIT','RUNNING'))
                     AND NOT EXISTS (SELECT 1 FROM durable_timer t
                         WHERE t.workflow_instance_id=w.id AND t.status='SCHEDULED')
                   ORDER BY w.id LIMIT ?""", (limit,),
            )]

    def dead_letter_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.transaction():
            return [decode(row) for row in self.db.execute(
                """SELECT id,event_id,event_type,attempts,max_attempts,last_error,created_at
                   FROM outbox_event WHERE status='DEAD_LETTER' ORDER BY id LIMIT ?""",
                (limit,),
            )]

    def redrive_outbox(self, outbox_id: int) -> bool:
        """Return a dead-lettered event to the delivery queue."""
        with self.transaction():
            changed = self.db.execute(
                """UPDATE outbox_event SET status='PENDING',attempts=0,next_attempt_at=?,
                   claimed_by=NULL,claimed_at=NULL WHERE id=? AND status='DEAD_LETTER'""",
                (utcnow(), outbox_id),
            )
            return changed.rowcount == 1

    def operational_counters(self) -> dict[str, int]:
        """Counters an operator needs before anything is on fire."""
        with self.transaction():
            def scalar(sql: str, *args: Any) -> int:
                return int(self.db.execute(sql, args).fetchone()[0] or 0)

            now = utcnow()
            return {
                "workflows_running": scalar(
                    "SELECT COUNT(*) FROM workflow_instance WHERE execution_status='RUNNING'"),
                "workflows_suspended": scalar(
                    "SELECT COUNT(*) FROM workflow_instance WHERE execution_status='SUSPENDED'"),
                "workflows_failed": scalar(
                    "SELECT COUNT(*) FROM workflow_instance WHERE execution_status='FAILED'"),
                "workflows_stuck": len(self.stuck_workflows(limit=1000)),
                "jobs_queued": scalar(
                    "SELECT COUNT(*) FROM automation_job WHERE status IN ('QUEUED','RETRY_WAIT')"),
                "jobs_running": scalar(
                    "SELECT COUNT(*) FROM automation_job WHERE status='RUNNING'"),
                "jobs_lease_expired": scalar(
                    "SELECT COUNT(*) FROM automation_job WHERE status='RUNNING' AND lease_expires_at<?",
                    now),
                "timers_due": scalar(
                    "SELECT COUNT(*) FROM durable_timer WHERE status='SCHEDULED' AND due_at<=?", now),
                "outbox_pending": scalar(
                    "SELECT COUNT(*) FROM outbox_event WHERE status IN ('PENDING','CLAIMED')"),
                "outbox_dead_letter": scalar(
                    "SELECT COUNT(*) FROM outbox_event WHERE status='DEAD_LETTER'"),
                "inbox_failed": scalar(
                    "SELECT COUNT(*) FROM inbox_event WHERE status='FAILED'"),
            }

    def requeue_job_for_step(self, step_id: int) -> bool:
        """Put a step's automation job back on the queue with fresh attempts."""
        changed = self.db.execute(
            """UPDATE automation_job SET status='QUEUED',attempt_count=0,available_at=?,
               claimed_by=NULL,claimed_at=NULL,lease_expires_at=NULL,completed_at=NULL
               WHERE step_instance_id=?""",
            (utcnow(), step_id),
        )
        return changed.rowcount > 0

    def create_subscription(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.transaction():
            item_id = self.db.execute(
                """INSERT INTO webhook_subscription(name,target_url,event_types_json,secret)
                   VALUES (?,?,?,?)""",
                (data["name"], data["target_url"], json.dumps(data.get("event_types", [])), data.get("secret")),
            ).lastrowid
            return redact_subscription(decode(self.db.execute(
                "SELECT * FROM webhook_subscription WHERE id=?", (item_id,)).fetchone()))

    def list_subscriptions(self, active_only: bool = False,
                           include_secrets: bool = False) -> list[dict[str, Any]]:
        """Subscriptions, with signing secrets redacted unless asked for.

        Only the delivery worker needs the secret. Every read interface that
        reaches a client leaves include_secrets false.
        """
        with self.transaction():
            sql = "SELECT * FROM webhook_subscription" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
            rows = [decode(row) for row in self.db.execute(sql)]
        return rows if include_secrets else [redact_subscription(row) for row in rows]

    def delivered_subscription_ids(self, outbox_id: int) -> set[int]:
        """Subscriptions that have already accepted this event, so a retry skips them."""
        with self.transaction():
            return {row[0] for row in self.db.execute(
                """SELECT subscription_id FROM outbox_delivery
                   WHERE outbox_event_id=? AND status='DELIVERED'""", (outbox_id,),
            )}

    def pending_outbox(self, limit: int = 50, worker_id: str = "delivery-worker") -> list[dict[str, Any]]:
        with self.transaction():
            now = utcnow()
            stale = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            rows = list(self.db.execute(
                """SELECT id FROM outbox_event WHERE
                   (status='PENDING' AND next_attempt_at<=?)
                   OR (status='CLAIMED' AND claimed_at<?)
                   ORDER BY id LIMIT ?""", (now, stale, limit),
            ))
            result = []
            for row in rows:
                self.db.execute(
                    """UPDATE outbox_event SET status='CLAIMED',claimed_by=?,claimed_at=?
                       WHERE id=? AND status IN ('PENDING','CLAIMED')""", (worker_id, now, row["id"]),
                )
                result.append(decode(self.db.execute("SELECT * FROM outbox_event WHERE id=?", (row["id"],)).fetchone()))
            return result

    def record_delivery(self, outbox_id: int, subscription_id: int, delivered: bool,
                        error: str | None = None) -> None:
        with self.transaction():
            existing = self.db.execute(
                "SELECT id,attempts FROM outbox_delivery WHERE outbox_event_id=? AND subscription_id=?",
                (outbox_id, subscription_id),
            ).fetchone()
            status = "DELIVERED" if delivered else "FAILED"
            if existing:
                self.db.execute(
                    """UPDATE outbox_delivery SET status=?,attempts=?,last_error=?,
                       delivered_at=CASE WHEN ? THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') END WHERE id=?""",
                    (status, existing["attempts"] + 1, error, delivered, existing["id"]),
                )
            else:
                self.db.execute(
                    """INSERT INTO outbox_delivery
                       (outbox_event_id,subscription_id,status,attempts,last_error,delivered_at)
                       VALUES (?,?,?,?,?,CASE WHEN ? THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') END)""",
                    (outbox_id, subscription_id, status, 1, error, delivered),
                )

    def mark_outbox(self, outbox_id: int, delivered: bool, error: str | None = None) -> None:
        with self.transaction():
            row = self.db.execute(
                "SELECT attempts,max_attempts FROM outbox_event WHERE id=?", (outbox_id,)
            ).fetchone()
            attempts = row["attempts"] + 1
            if delivered:
                self.db.execute(
                    """UPDATE outbox_event SET status='DELIVERED',attempts=?,processed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                       claimed_by=NULL,claimed_at=NULL,last_error=NULL WHERE id=?""", (attempts, outbox_id),
                )
            elif attempts >= row["max_attempts"]:
                self.db.execute(
                    """UPDATE outbox_event SET status='DEAD_LETTER',attempts=?,last_error=?,
                       claimed_by=NULL,claimed_at=NULL WHERE id=?""", (attempts, error, outbox_id),
                )
            else:
                delay = min(3600, 2 ** min(attempts, 10))
                next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                self.db.execute(
                    """UPDATE outbox_event SET status='PENDING',attempts=?,last_error=?,next_attempt_at=?,
                       claimed_by=NULL,claimed_at=NULL WHERE id=?""",
                    (attempts, error, next_at, outbox_id),
                )


@contextmanager
def _null_context():
    yield
