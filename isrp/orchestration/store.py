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

from .actor import Actor
from .states import (
    ASSESSMENT, DEFAULT_EXECUTION_MODE, Owner, REQUEST, resolve_execution_mode,
)
from .errors import ConflictError, ValidationError

from .schema import (
    EVENT_SCHEMA_VERSION, INDEXES, MIGRATIONS, SCHEMA, SCHEMA_VERSION, TABLES,
)


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


def timestamp(moment: datetime) -> str:
    """Canonical sortable UTC timestamp, matching the SQL default exactly."""
    return moment.astimezone(timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def utcnow() -> str:
    """The current time in the one persisted timestamp format.

    Mixing isoformat() with SQLite's CURRENT_TIMESTAMP produced two shapes in
    the same column, and due_at comparisons are string comparisons.
    """
    return timestamp(datetime.now(timezone.utc))


def bounded_limit(limit: int, maximum: int = 1000) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
        raise ValidationError(f"limit must be between 1 and {maximum}")
    return limit


def valid_offset(offset: int) -> int:
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValidationError("offset must be zero or greater")
    return offset


def valid_worker_id(worker_id: str) -> str:
    if not isinstance(worker_id, str) or not worker_id.strip():
        raise ValidationError("worker_id must be a non-empty string")
    return worker_id


# Every object this schema owns. Test isolation and the odd shared database
# namespace them with a prefix, which is rewritten in one place on the way to
# the database rather than templated into a hundred inline statements.
FLOW_TABLES = (
    "fsm_definition", "fsm_version", "fsm_state_definition", "fsm_transition_definition",
    "workflow_definition", "workflow_version", "step_definition", "transition_definition",
    "isrp_request", "isrp_assessment", "workflow_fact_history", "step_instance",
    "step_attempt", "work_assignment", "work_candidate", "signal_receipt",
    "automation_job", "durable_timer", "event_log", "workflow_command",
    # Names earlier versions used. They are still rewritten so that a prefixed
    # database can be migrated forward off them.
    "workflow_instance", "workflow_subject", "workflow_event",
    "inbox_event", "outbox_event", "webhook_subscription", "outbox_delivery",
    "schema_metadata",
)

# The two aggregates ISRP orchestrates, and the table each one lives in.
OWNER_TABLES = {REQUEST: "isrp_request", ASSESSMENT: "isrp_assessment"}


def owner_table(owner_type: str) -> str:
    try:
        return OWNER_TABLES[owner_type]
    except KeyError:
        raise ValidationError(
            f"Unknown owner type {owner_type!r}; ISRP orchestrates "
            f"{' and '.join(OWNER_TABLES)}") from None


def as_owner(value: Any) -> Owner:
    """Accept ('ISRP_REQUEST', 3) as readily as Owner('ISRP_REQUEST', 3)."""
    owner = value if isinstance(value, Owner) else Owner(*value)
    owner_table(owner.type)
    return owner


def running_owner_sql(alias: str) -> str:
    """SQL that is true when the row's owner aggregate is still running.

    Two owner tables means no single join. An EXISTS per owner type keeps the
    condition in one place instead of in each of the queries that need it.
    """
    return "(" + " OR ".join(
        f"({alias}.owner_type='{owner_type}' AND EXISTS (SELECT 1 FROM {table} o "
        f"WHERE o.id={alias}.owner_id AND o.execution_status='RUNNING'))"
        for owner_type, table in OWNER_TABLES.items()) + ")"
# Longest first so that no name is a prefix of another match. A trailing word
# boundary keeps workflow_instance_id from matching workflow_instance.
_FLOW_OBJECT = re.compile(
    r"\b(" + "|".join(sorted(FLOW_TABLES, key=len, reverse=True)) + r"|idx_\w+)\b")


def apply_prefix(sql: str, prefix: str) -> str:
    return sql if not prefix else _FLOW_OBJECT.sub(lambda m: prefix + m.group(0), sql)


class _PrefixedConnection:
    """Rewrites orchestration object names on the way to the database.

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


TERMINAL_STEP_EXECUTION = ("COMPLETED", "SKIPPED", "FAILED", "CANCELLED")


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
        """Standalone with a path; bound to a caller's connection with none.

        ISRP binds its own connection with using(), and orchestration never
        opens, commits, rolls back or closes one, so a domain write and a step
        transition commit as one unit. A table_prefix namespaces the tables,
        which is how the tests keep runs apart.
        """
        if table_prefix and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_prefix):
            raise ValidationError(
                "table_prefix must contain only letters, numbers, and underscores "
                "and must not start with a number")
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
                "This repository is connection-bound and has no database path. "
                "ISRP must bind its connection: `with repository.using(connection):`"
            )
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        """Standalone convenience: open a connection, install the schema, close it.

        ISRP calls create_schema() from its own migration step instead, so
        there is one schema with one version stamp rather than two.
        """
        connection = self._connect()
        try:
            self.create_schema(connection)
        finally:
            connection.close()

    def create_schema(self, connection: sqlite3.Connection) -> None:
        """Install or upgrade the schema on a caller-supplied connection.

        Safe to call on every start. The legacy 0.2 migration rewrites state
        derived from column values, so it runs only for a populated database
        that predates version stamping, and never again afterwards.

        This must not run inside an open transaction: schema changes commit
        implicitly in SQLite, which would commit whatever the caller had in
        flight alongside them.
        """
        if connection.in_transaction:
            raise RuntimeError(
                "create_schema cannot run inside an open transaction: DDL commits "
                "implicitly and would commit the caller's uncommitted work with it"
            )
        existing_version = self._existing_schema_version(connection)
        if existing_version is not None and existing_version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {existing_version} is newer than this "
                f"runtime's version {SCHEMA_VERSION}")
        wrapped = self._wrap(connection)
        legacy = self._legacy_database(connection)
        wrapped.executescript(SCHEMA)
        # Version 7 rebuilds step_instance, which seven other tables reference.
        # SQLite rewrites those REFERENCES clauses on rename, and enforces child
        # rows on drop, only while foreign key enforcement is on. Turning it off
        # around the chain is the procedure SQLite documents for altering a
        # referenced table, and the pragma is a no-op inside a transaction, so
        # it is set here where none is open rather than inside the migration.
        enforcing = bool(connection.execute("PRAGMA foreign_keys").fetchone()[0])
        if enforcing:
            connection.execute("PRAGMA foreign_keys=OFF")
        try:
            with self.using(connection):
                version = self._schema_version()
                # The built-in FSM rows written below include the v4 category
                # column, so a stamped v3 database must receive that structural
                # migration before those rows can be ensured.
                if version is not None and version < 4:
                    self._migrate_to_4_state_category()
                    version = 4
                lifecycle_id = self._ensure_fsm(DEFAULT_LIFECYCLE_FSM, "WORKFLOW")
                step_id = self._ensure_fsm(DEFAULT_STEP_FSM, "STEP")
                if version is None:
                    # A fresh database is already at the current shape. Only a
                    # populated one that predates stamping needs the 0.2
                    # migration, and it then enters the chain below at zero.
                    if legacy:
                        self._migrate_legacy_schema(lifecycle_id, step_id)
                    version = 0 if legacy else SCHEMA_VERSION
                for target, method in MIGRATIONS:
                    if version < target:
                        getattr(self, method)()
                        version = target
                self._stamp_schema_version()
            connection.commit()
        finally:
            if enforcing:
                connection.execute("PRAGMA foreign_keys=ON")
        # Indexes last: on an older database some of them reference columns and
        # tables the migration has only just created.
        wrapped.executescript(INDEXES)
        connection.commit()

    @contextmanager
    def using(self, connection: sqlite3.Connection) -> Iterator[None]:
        """Bind a caller-owned connection for the duration of the block.

        Orchestration reads and writes through it but never commits, rolls back
        or closes it. ISRP decides where the transaction ends, which is what
        lets a domain write and a step transition commit as one unit.
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

    def _existing_schema_version(self, connection: sqlite3.Connection) -> int | None:
        table = f"{self.table_prefix}schema_metadata"
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            return None
        row = connection.execute(
            f'SELECT value FROM "{table}" WHERE key=?', ("schema_version",)
        ).fetchone()
        return int(row[0]) if row else None

    def _migrate_to_4_state_category(self) -> None:
        """Complete the v3-to-v4 step that introduced explicit state categories."""
        columns = {row["name"] for row in self.db.execute(
            "PRAGMA table_info(fsm_state_definition)")}
        if columns and "category" not in columns:
            self.db.execute(
                "ALTER TABLE fsm_state_definition ADD COLUMN category TEXT")

    def _migrate_to_5_shared_event_log(self) -> None:
        """One event log, one outbox, and one inbox for all ISRP modules.

        The outbox and inbox gain columns. The event log cannot: its workflow
        column was NOT NULL and its uniqueness was per workflow, neither of
        which SQLite can alter in place, so rows are copied into the new table
        and the old one is dropped.
        """
        for table, columns in (
            ("outbox_event", (("aggregate_type", "TEXT"), ("aggregate_id", "TEXT"),
                              ("aggregate_version", "INTEGER"), ("correlation_id", "TEXT"))),
            ("inbox_event", (("correlation_id", "TEXT"), ("next_attempt_at", "TEXT"),
                             ("claimed_by", "TEXT"), ("claimed_at", "TEXT"))),
        ):
            existing = {row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")}
            for name, ddl in columns:
                if name not in existing:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

        present = {row[0] for row in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        legacy_name = f"{self.table_prefix}workflow_event"
        if legacy_name in present:
            self.db.execute(
                """INSERT INTO event_log
                   (event_id,aggregate_type,aggregate_id,sequence_number,
                    step_instance_id,event_type,actor,actor_org_id,
                    previous_state,new_state,payload_json,created_at)
                   SELECT event_id,'WORKFLOW',CAST(workflow_instance_id AS TEXT),
                          sequence_number,step_instance_id,event_type,
                          actor,actor_org_id,previous_state,new_state,payload_json,created_at
                   FROM workflow_event""")
            self.db.execute("DROP TABLE workflow_event")

    def _migrate_to_6_execution_mode(self) -> None:
        """Record who performs each step, so publication can police it."""
        existing = {row["name"] for row in self.db.execute(
            "PRAGMA table_info(step_definition)")}
        if "execution_mode" not in existing:
            self.db.execute("ALTER TABLE step_definition ADD COLUMN execution_mode TEXT")
        for step_type, mode in DEFAULT_EXECUTION_MODE.items():
            self.db.execute(
                """UPDATE step_definition SET execution_mode=?
                   WHERE step_type=? AND execution_mode IS NULL""", (mode, step_type))

    def _rebuild(self, table: str, columns: str, select: str) -> None:
        """Replace a table with its current definition, carrying its rows over.

        SQLite cannot drop a column that a unique constraint covers, and every
        table here is changing both at once, so each is rebuilt rather than
        altered. The definition is taken from the schema module so that a
        rebuilt table and a freshly created one cannot drift apart.

        The rename runs under `legacy_alter_table`. Without it SQLite rewrites
        every other table's REFERENCES clause to follow the renamed table, so
        the seven tables that point at step_instance would end up pointing at
        step_instance_prior and keep pointing there after it was dropped. That
        is not hypothetical: it is what happened the first time this ran, and
        turning foreign keys off does not prevent it.
        """
        old = f"{self.table_prefix}{table}_prior"
        self.db.execute("PRAGMA legacy_alter_table=ON")
        self.db.execute(f"ALTER TABLE {table} RENAME TO {old}")
        self.db.execute("PRAGMA legacy_alter_table=OFF")
        self.db.execute(TABLES[table])
        self.db.execute(f"INSERT INTO {table}({columns}) SELECT {select} FROM {old}")
        self.db.execute(f"DROP TABLE {old}")

    @staticmethod
    def _owner_type_sql(column: str) -> str:
        """Which aggregate an old workflow id landed in.

        Ids carry over unchanged, so the table a row is now in is the answer.
        """
        return (f"CASE WHEN EXISTS (SELECT 1 FROM isrp_request r WHERE r.id={column}) "
                f"THEN '{REQUEST}' ELSE '{ASSESSMENT}' END")

    def _migrate_to_7_owner_aggregates(self) -> None:
        """Fold workflow_instance into the two aggregates ISRP orchestrates.

        A run with no parent becomes a request; a run with a parent becomes an
        assessment of the request at the root of its tree. A tree deeper than
        two levels cannot be represented and is flattened onto that same root.
        That is the only lossy part of this migration, and it is recorded here
        rather than discovered later: a flattened grandchild also loses its
        parent step pointer, because that node sits on another assessment and
        would otherwise be a node the request can never satisfy.

        Ids carry over unchanged. They all came from one sequence, so they stay
        unique across the two tables, which means every runtime row can find
        its owner type by asking which table its old id landed in and no id map
        is needed.
        """
        def carries_workflow_id(table: str) -> bool:
            return any(row["name"] == "workflow_instance_id"
                       for row in self.db.execute(f"PRAGMA table_info({table})"))

        present = {row[0] for row in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        folding = f"{self.table_prefix}workflow_instance" in present

        roots = """WITH RECURSIVE ancestry(id, root_id) AS (
                       SELECT id, id FROM workflow_instance
                        WHERE parent_workflow_instance_id IS NULL
                       UNION ALL
                       SELECT w.id, a.root_id FROM workflow_instance w
                         JOIN ancestry a ON a.id = w.parent_workflow_instance_id)"""
        if folding:
            self._fold_workflow_instance(roots)

        owner = self._owner_type_sql("workflow_instance_id")
        for table, columns in (
            ("step_instance",
             "id,step_definition_id,iteration_number,state,execution_status,result_json,"
             "activated_at,started_at,completed_at"),
            ("workflow_fact_history",
             "id,fact_key,previous_value_json,new_value_json,source_type,source_reference,"
             "actor,revision,created_at"),
            ("signal_receipt",
             "id,command_id,signal_type,correlation_key,payload_json,received_at,"
             "consumed_at,consumed_step_instance_id"),
            ("automation_job",
             "id,job_key,step_instance_id,handler,status,attempt_count,max_attempts,"
             "input_json,result_json,available_at,claimed_by,claimed_at,lease_expires_at,"
             "completed_at,last_error"),
            ("durable_timer",
             "id,timer_key,step_instance_id,timer_type,action,due_at,payload_json,"
             "status,fired_at,cancelled_at"),
        ):
            if carries_workflow_id(table):
                self._rebuild(table, f"owner_type,owner_id,{columns}",
                              f"{owner},workflow_instance_id,{columns}")

        # A receipt says which aggregate a command hit; the revision it also
        # carried is informational, because a replay returns current state
        # rather than a snapshot. Migrated receipts therefore lose it rather
        # than depending on a JSON extension being compiled in.
        if carries_workflow_id("workflow_command"):
            self._rebuild(
                "workflow_command", "command_id,owner_type,owner_id,action,processed_at",
                f"command_id,{owner},workflow_instance_id,action,processed_at")

        # The WORKFLOW aggregate type is gone: an execution event belongs to
        # the request or the assessment it drove, in the same stream as that
        # aggregate's own domain events. Without a workflow_instance table
        # there is no parent anywhere to read, so every run was a top-level
        # one, which is a request.
        aggregate = (self._owner_type_sql("CAST(aggregate_id AS INTEGER)")
                     if folding else f"'{REQUEST}'")
        if carries_workflow_id("event_log"):
            self._rebuild(
                "event_log",
                "id,event_id,aggregate_type,aggregate_id,sequence_number,step_instance_id,"
                "event_type,actor,actor_org_id,actor_role,correlation_id,command_id,"
                "previous_state,new_state,previous_revision,new_revision,changed_fields_json,"
                "reason_reference,source_channel,payload_json,created_at",
                f"id,event_id,CASE WHEN aggregate_type='WORKFLOW' THEN {aggregate} "
                "ELSE aggregate_type END,aggregate_id,sequence_number,step_instance_id,"
                "event_type,actor,actor_org_id,actor_role,correlation_id,command_id,"
                "previous_state,new_state,previous_revision,new_revision,changed_fields_json,"
                "reason_reference,source_channel,payload_json,created_at")
        else:
            self.db.execute(
                f"""UPDATE event_log SET aggregate_type={aggregate}
                    WHERE aggregate_type='WORKFLOW'""")
        self.db.execute(
            f"""UPDATE outbox_event SET aggregate_type={aggregate}
                WHERE aggregate_type='WORKFLOW'""")

        self.db.execute("DROP TABLE IF EXISTS workflow_subject")
        if folding:
            self.db.execute("DROP TABLE workflow_instance")

    def _migrate_to_8_command_fingerprint(self) -> None:
        columns = {row["name"] for row in self.db.execute(
            "PRAGMA table_info(workflow_command)")}
        if "request_fingerprint" not in columns:
            self.db.execute(
                "ALTER TABLE workflow_command ADD COLUMN request_fingerprint TEXT")

    def _migrate_to_9_request_intake(self) -> None:
        """Add the first ISRP business fields beside request execution state."""
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(isrp_request)")}
        additions = {
            "reference": "TEXT",
            "summary": "TEXT NOT NULL DEFAULT ''",
            "requester_name": "TEXT NOT NULL DEFAULT ''",
            "requester_email": "TEXT NOT NULL DEFAULT ''",
            "organization_name": "TEXT NOT NULL DEFAULT ''",
            "source_system": "TEXT NOT NULL DEFAULT 'ISRP'",
            "submitted_at": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                self.db.execute(
                    f"ALTER TABLE isrp_request ADD COLUMN {name} {declaration}")

    def _fold_workflow_instance(self, roots: str) -> None:
        self.db.execute(
            """INSERT INTO isrp_request
               (id,workflow_version_id,title,lifecycle_status,execution_status,
                current_stage,revision,variables_json,created_by,created_at,
                completed_at,suspended_at,cancelled_at)
               SELECT id,workflow_version_id,title,lifecycle_state,execution_status,
                      current_stage,revision,variables_json,created_by,created_at,
                      completed_at,suspended_at,cancelled_at
                 FROM workflow_instance WHERE parent_workflow_instance_id IS NULL""")
        self.db.execute(
            roots + """
            INSERT INTO isrp_assessment
               (id,request_id,assessment_type,parent_step_instance_id,required_flag,
                workflow_version_id,title,lifecycle_status,execution_status,
                current_stage,revision,variables_json,created_by,created_at,
                completed_at,suspended_at,cancelled_at)
               SELECT w.id,COALESCE(a.root_id,w.parent_workflow_instance_id),
                      w.relationship_type,
                      CASE WHEN w.parent_workflow_instance_id
                                = COALESCE(a.root_id,w.parent_workflow_instance_id)
                           THEN w.parent_step_instance_id END,
                      w.required_flag,
                      w.workflow_version_id,w.title,w.lifecycle_state,w.execution_status,
                      w.current_stage,w.revision,w.variables_json,w.created_by,w.created_at,
                      w.completed_at,w.suspended_at,w.cancelled_at
                 FROM workflow_instance w LEFT JOIN ancestry a ON a.id=w.id
                WHERE w.parent_workflow_instance_id IS NOT NULL""")

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
            if not existing:
                # The table is absent, so there is nothing to bring forward.
                # workflow_event is the case that matters: a 0.2 database has
                # it, a newer schema creates event_log instead.
                continue
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
                    join_rule,step_fsm_version_id,configuration_json,execution_mode)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (version_id, step["key"], step["name"], step["type"], step.get("stage", ""),
                 step.get("description", ""), step.get("assignment_role"), step.get("join_rule"),
                 step_fsm_id, json.dumps(step.get("configuration", {})),
                 resolve_execution_mode(step["type"], step.get("execution_mode"))),
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

        A receipt records which aggregate the command hit and the revision it
        produced. It deliberately does not hold a copy of that aggregate:
        storing a full snapshot per command made command storage grow with the
        square of the number of commands.
        """
        row = self.db.execute(
            """SELECT owner_type,owner_id,action,revision,request_fingerprint
               FROM workflow_command
               WHERE command_id=?""", (command_id,)).fetchone()
        return dict(row) if row else None

    def record_command(self, command_id: str, owner: Any, action: str,
                       revision: int | None = None,
                       request_fingerprint: str | None = None) -> None:
        owner = as_owner(owner)
        self.db.execute(
            """INSERT INTO workflow_command
               (command_id,owner_type,owner_id,action,revision,request_fingerprint)
               VALUES (?,?,?,?,?,?)""",
            (command_id, owner.type, owner.id, action, revision, request_fingerprint))

    def create_aggregate(self, owner_type: str, data: dict[str, Any],
                         version: dict[str, Any]) -> Owner:
        """Start a request or an assessment on a published workflow version.

        Only the orchestration columns are written. ISRP's own columns on the
        same row are its to fill, in the same transaction.
        """
        table = owner_table(owner_type)
        columns: dict[str, Any] = {
            "workflow_version_id": data["workflow_version_id"],
            "title": data["title"],
            "lifecycle_status": self.fsm_initial_state(version["lifecycle_fsm_version_id"]),
            "variables_json": json.dumps(data.get("variables", {})),
            "created_by": Actor.from_value(data.get("actor", "system")).actor_id,
        }
        if owner_type == REQUEST:
            columns.update({
                "reference": data.get("reference"),
                "summary": data.get("summary", ""),
                "requester_name": data.get("requester_name", ""),
                "requester_email": data.get("requester_email", ""),
                "organization_name": data.get("organization_name", ""),
                "source_system": data.get("source_system", "ISRP"),
            })
        if owner_type == ASSESSMENT:
            columns.update({
                "request_id": data["request_id"],
                "assessment_type": data.get("assessment_type"),
                "parent_step_instance_id": data.get("parent_step_instance_id"),
                "required_flag": bool(data.get("required", True)),
            })
        names = ",".join(columns)
        new_id = self.db.execute(
            f"INSERT INTO {table}({names}) VALUES ({','.join('?' * len(columns))})",
            tuple(columns.values()),
        ).lastrowid
        return Owner(owner_type, new_id)

    def create_step_instances(self, owner: Any, version_id: int) -> None:
        owner = as_owner(owner)
        for row in self.db.execute(
            "SELECT id,step_fsm_version_id FROM step_definition WHERE workflow_version_id=?", (version_id,)
        ):
            initial = self.fsm_initial_state(row["step_fsm_version_id"])
            self.db.execute(
                """INSERT INTO step_instance
                   (owner_type,owner_id,step_definition_id,state,execution_status)
                   VALUES (?,?,?,?,'NOT_READY')""",
                (owner.type, owner.id, row["id"], initial),
            )

    def fsm_has_state(self, fsm_version_id: int, state: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM fsm_state_definition WHERE fsm_version_id=? AND state_key=?",
            (fsm_version_id, state),
        ).fetchone() is not None

    def remap_step_instances(self, owner: Any, target_version_id: int) -> dict[str, int]:
        """Repoint an aggregate's nodes at another version's definitions, by step key.

        Step key is the identity that survives a version change: node ids do
        not, and node order certainly does not. A node whose key exists in both
        versions keeps its state; one that has been removed is retired; one
        that is new starts unready.
        """
        owner = as_owner(owner)
        current = {row["step_key"]: dict(row) for row in self.db.execute(
            """SELECT si.id,si.state,si.execution_status,sd.step_key
               FROM step_instance si JOIN step_definition sd ON sd.id=si.step_definition_id
               WHERE si.owner_type=? AND si.owner_id=?""", (owner.type, owner.id))}
        target = {row["step_key"]: dict(row) for row in self.db.execute(
            """SELECT id,step_key,step_fsm_version_id FROM step_definition
               WHERE workflow_version_id=?""", (target_version_id,))}

        summary = {"kept": 0, "added": 0, "retired": 0}
        placeholders = ",".join("?" * len(TERMINAL_STEP_EXECUTION))
        for key, row in current.items():
            if key in target:
                if not self.fsm_has_state(target[key]["step_fsm_version_id"], row["state"]):
                    raise ConflictError(
                        f"Target step '{key}' FSM does not contain current state "
                        f"'{row['state']}'")
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
                       (owner_type,owner_id,step_definition_id,state,execution_status)
                       VALUES (?,?,?,?,'NOT_READY')""",
                    (owner.type, owner.id, row["id"],
                     self.fsm_initial_state(row["step_fsm_version_id"])))
                summary["added"] += 1
        return summary

    def get_aggregate_row(self, owner: Any) -> dict[str, Any] | None:
        """One aggregate's own row, with the owner type it was read under."""
        owner = as_owner(owner)
        row = decode(self.db.execute(
            f"SELECT * FROM {owner_table(owner.type)} WHERE id=?", (owner.id,)).fetchone())
        if row is not None:
            row["owner_type"] = owner.type
        return row

    def list_aggregates(self, owner_type: str) -> list[dict[str, Any]]:
        with self.transaction():
            return [dict(decode(row), owner_type=owner_type) for row in self.db.execute(
                f"""SELECT a.*,wd.name workflow_name FROM {owner_table(owner_type)} a
                    JOIN workflow_version wv ON wv.id=a.workflow_version_id
                    JOIN workflow_definition wd ON wd.id=wv.definition_id ORDER BY a.id DESC"""
            )]

    def get_aggregate(self, owner: Any) -> dict[str, Any] | None:
        owner = as_owner(owner)
        context = self.transaction() if self._connection is None else _null_context()
        with context:
            aggregate = decode(self.db.execute(
                f"""SELECT a.*,wd.name workflow_name,wv.version_number,wv.lifecycle_fsm_version_id
                    FROM {owner_table(owner.type)} a
                    JOIN workflow_version wv ON wv.id=a.workflow_version_id
                    JOIN workflow_definition wd ON wd.id=wv.definition_id WHERE a.id=?""",
                (owner.id,),
            ).fetchone())
            if not aggregate:
                return None
            aggregate["owner_type"] = owner.type
            aggregate["assessments"] = [decode(row) for row in self.db.execute(
                """SELECT id,title,assessment_type,lifecycle_status,execution_status,
                   parent_step_instance_id,required_flag
                   FROM isrp_assessment WHERE request_id=? ORDER BY id""", (owner.id,)
            )] if owner.type == REQUEST else []
            aggregate["steps"] = []
            for row in self.db.execute(
                """SELECT si.*,sd.step_key,sd.name,sd.step_type,sd.stage,sd.description,
                   sd.assignment_role,sd.join_rule,sd.execution_mode,sd.step_fsm_version_id,
                   sd.configuration_json
                   FROM step_instance si JOIN step_definition sd ON sd.id=si.step_definition_id
                   WHERE si.owner_type=? AND si.owner_id=? ORDER BY sd.id""",
                (owner.type, owner.id),
            ):
                step = decode(row)
                step["assignments"] = [decode(item) for item in self.db.execute(
                    "SELECT * FROM work_assignment WHERE step_instance_id=? ORDER BY id", (row["id"],)
                )]
                step["candidates"] = [decode(item) for item in self.db.execute(
                    "SELECT * FROM work_candidate WHERE step_instance_id=? ORDER BY id", (row["id"],)
                )]
                aggregate["steps"].append(step)
            aggregate["events"] = [decode(row) for row in self.db.execute(
                """SELECT * FROM event_log WHERE aggregate_type=? AND aggregate_id=?
                   ORDER BY sequence_number DESC""", (owner.type, str(owner.id)),
            )]
            return aggregate

    def get_step(self, step_id: int) -> dict[str, Any] | None:
        return decode(self.db.execute(
            """SELECT si.*,sd.step_key,sd.step_type,sd.stage,sd.assignment_role,sd.join_rule,
               sd.step_fsm_version_id,sd.configuration_json
               FROM step_instance si JOIN step_definition sd ON sd.id=si.step_definition_id
               WHERE si.id=?""", (step_id,),
        ).fetchone())

    def list_steps_by_execution(self, owner: Any, status: str) -> list[dict[str, Any]]:
        owner = as_owner(owner)
        return [decode(row) for row in self.db.execute(
            """SELECT si.*,sd.step_key,sd.step_type,sd.stage,sd.assignment_role,sd.join_rule,
               sd.step_fsm_version_id,sd.configuration_json
               FROM step_instance si JOIN step_definition sd ON sd.id=si.step_definition_id
               WHERE si.owner_type=? AND si.owner_id=? AND si.execution_status=?""",
            (owner.type, owner.id, status),
        )]

    def incoming(self, owner: Any, step_definition_id: int) -> list[dict[str, Any]]:
        owner = as_owner(owner)
        return [decode(row) for row in self.db.execute(
            """SELECT td.condition_json,pred.execution_status,pred.state,
                      pred.id step_instance_id,sd.configuration_json
               FROM transition_definition td
               JOIN step_instance pred
                 ON pred.step_definition_id=td.from_step_id
                AND pred.owner_type=? AND pred.owner_id=?
               JOIN step_definition sd ON sd.id=td.from_step_id
               WHERE td.to_step_id=?""", (owner.type, owner.id, step_definition_id),
        )]

    def update_step(self, step_id: int, values: dict[str, Any]) -> None:
        values = dict(values)
        if "result" in values:
            values["result_json"] = json.dumps(values.pop("result"))
        sql = ",".join(f"{key}=?" for key in values)
        self.db.execute(f"UPDATE step_instance SET {sql} WHERE id=?", (*values.values(), step_id))

    def update_aggregate(self, owner: Any, values: dict[str, Any]) -> None:
        owner = as_owner(owner)
        values = dict(values)
        if "variables" in values:
            values["variables_json"] = json.dumps(values.pop("variables"))
        sql = ",".join(f"{key}=?" for key in values)
        self.db.execute(
            f"UPDATE {owner_table(owner.type)} SET {sql} WHERE id=?",
            (*values.values(), owner.id))

    def set_facts(self, aggregate: dict[str, Any], facts: dict[str, Any], actor: str,
                  source_type: str, source_reference: str | None) -> None:
        owner = Owner(aggregate["owner_type"], aggregate["id"])
        current = dict(aggregate["variables"])
        revision = aggregate["revision"] + 1
        for key, value in facts.items():
            previous = current.get(key)
            if previous == value:
                continue
            self.db.execute(
                """INSERT INTO workflow_fact_history
                   (owner_type,owner_id,fact_key,previous_value_json,new_value_json,
                    source_type,source_reference,actor,revision) VALUES (?,?,?,?,?,?,?,?,?)""",
                (owner.type, owner.id, key, json.dumps(previous), json.dumps(value),
                 source_type, source_reference, actor, revision),
            )
            current[key] = value
        self.update_aggregate(owner, {"variables": current, "revision": revision})

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

    def insert_signal(self, command: dict[str, Any], owner: Any) -> None:
        owner = as_owner(owner)
        self.db.execute(
            """INSERT INTO signal_receipt
               (command_id,owner_type,owner_id,signal_type,correlation_key,payload_json)
               VALUES (?,?,?,?,?,?)""",
            (command["command_id"], owner.type, owner.id, command["signal_type"],
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

    def mark_inbox_processed(self, inbox_id: int, error: str | None = None,
                             worker_id: str | None = None) -> bool:
        claimant = " AND status='PROCESSING' AND claimed_by=?" if worker_id else ""
        args = ["PROCESSED" if error is None else "FAILED", error, error, inbox_id]
        if worker_id:
            args.append(worker_id)
        changed = self.db.execute(
            """UPDATE inbox_event SET status=?,attempts=attempts+1,
               processed_at=CASE WHEN ? IS NULL THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE processed_at END,
               last_error=?,claimed_by=NULL,claimed_at=NULL WHERE id=?""" + claimant,
            args,
        )
        return changed.rowcount == 1

    def unconsumed_signal(self, owner: Any, signal_type: str,
                          correlation_key: str | None) -> dict[str, Any] | None:
        owner = as_owner(owner)
        return decode(self.db.execute(
            """SELECT * FROM signal_receipt WHERE owner_type=? AND owner_id=? AND signal_type=?
               AND (? IS NULL OR correlation_key=?) AND consumed_at IS NULL ORDER BY id LIMIT 1""",
            (owner.type, owner.id, signal_type, correlation_key, correlation_key),
        ).fetchone())

    def consume_signal(self, signal_id: int, step_id: int) -> None:
        self.db.execute(
            """UPDATE signal_receipt SET consumed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),consumed_step_instance_id=?
               WHERE id=? AND consumed_at IS NULL""", (step_id, signal_id),
        )

    def create_automation_job(self, owner: Any, step: dict[str, Any]) -> None:
        owner = as_owner(owner)
        config = step.get("configuration", {})
        self.db.execute(
            """INSERT OR IGNORE INTO automation_job
               (job_key,owner_type,owner_id,step_instance_id,handler,max_attempts,input_json)
               VALUES (?,?,?,?,?,?,?)""",
            (f"{owner.type}:{owner.id}:{step['id']}:{step['iteration_number']}",
             owner.type, owner.id, step["id"],
             config.get("handler", "default"), int(config.get("max_attempts", 3)),
             json.dumps(config.get("input", {}))),
        )

    def claim_jobs(self, worker_id: str, limit: int = 10, lease_seconds: int = 60) -> list[dict[str, Any]]:
        valid_worker_id(worker_id)
        bounded_limit(limit)
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
            raise ValidationError("lease_seconds must be a positive whole number")
        now = utcnow()
        lease = timestamp(datetime.now(timezone.utc) + timedelta(seconds=lease_seconds))
        rows = list(self.db.execute(
            f"""SELECT j.id FROM automation_job j
                WHERE {running_owner_sql('j')} AND (
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

    def create_timer(self, owner: Any, step: dict[str, Any], due_at: str,
                     action: str = "COMPLETE_STEP") -> None:
        """A durable timer for a node.

        The due time is computed by the engine, which owns the business
        calendar. The key includes the action so that a node can carry both a
        delay timer and a service-level timer for the same iteration.
        """
        owner = as_owner(owner)
        config = step.get("configuration", {})
        self.db.execute(
            """INSERT OR IGNORE INTO durable_timer
               (timer_key,owner_type,owner_id,step_instance_id,timer_type,action,due_at,payload_json)
               VALUES (?,?,?,?,'RELATIVE',?,?,?)""",
            (f"{owner.type}:{owner.id}:{step['id']}:{step['iteration_number']}:{action}",
             owner.type, owner.id, step["id"], action, due_at,
             json.dumps(config.get("payload", {}))),
        )

    def due_timers(self, limit: int = 50) -> list[dict[str, Any]]:
        """Due timers for running aggregates only.

        A suspended request or assessment leaves its timers scheduled so that
        they fire once it resumes, rather than advancing something that is
        meant to be idle.
        """
        bounded_limit(limit)
        return [decode(row) for row in self.db.execute(
            f"""SELECT t.* FROM durable_timer t
                WHERE t.status='SCHEDULED' AND t.due_at<=? AND {running_owner_sql('t')}
                ORDER BY t.due_at,t.id LIMIT ?""", (utcnow(), limit),
        )]

    def cancel_open_work(self, owner: Any) -> None:
        """Cancel every unfinished node, timer, job and assignment of an aggregate.

        Execution status is the engine-controlled dimension, so it is the one
        set here. The FSM-controlled node state is left untouched, because a
        custom step FSM need not declare a cancelled state.
        """
        owner = as_owner(owner)
        stamp = utcnow()
        placeholders = ",".join("?" * len(TERMINAL_STEP_EXECUTION))
        self.db.execute(
            f"""UPDATE step_instance SET execution_status='CANCELLED',completed_at=?
                WHERE owner_type=? AND owner_id=?
                AND execution_status NOT IN ({placeholders})""",
            (stamp, owner.type, owner.id, *TERMINAL_STEP_EXECUTION),
        )
        self.db.execute(
            """UPDATE durable_timer SET status='CANCELLED',cancelled_at=?
               WHERE owner_type=? AND owner_id=? AND status='SCHEDULED'""",
            (stamp, owner.type, owner.id),
        )
        self.db.execute(
            """UPDATE automation_job SET status='CANCELLED',completed_at=?,lease_expires_at=NULL
               WHERE owner_type=? AND owner_id=? AND status IN ('QUEUED','RETRY_WAIT','RUNNING')""",
            (stamp, owner.type, owner.id),
        )
        self.db.execute(
            """UPDATE work_assignment SET status='CANCELLED',ended_at=?
               WHERE status='OPEN' AND step_instance_id IN
               (SELECT id FROM step_instance WHERE owner_type=? AND owner_id=?)""",
            (stamp, owner.type, owner.id),
        )

    def running_assessment_ids(self, request_id: int) -> list[int]:
        return [row[0] for row in self.db.execute(
            """SELECT id FROM isrp_assessment
               WHERE request_id=? AND execution_status='RUNNING' ORDER BY id""",
            (request_id,),
        )]

    def fire_timer(self, timer_id: int) -> None:
        self.db.execute(
            "UPDATE durable_timer SET status='FIRED',fired_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=? AND status='SCHEDULED'",
            (timer_id,),
        )

    def _correlation(self, owner: Owner) -> str:
        """One correlation id for a request and every assessment under it.

        A reviewer's determination on an assessment and the step transition it
        caused in the request belong to one investigation, so they carry one
        correlation id even though they are two aggregates.
        """
        if owner.type == REQUEST:
            return f"{REQUEST}:{owner.id}"
        row = self.db.execute(
            "SELECT request_id FROM isrp_assessment WHERE id=?", (owner.id,)).fetchone()
        return f"{REQUEST}:{row['request_id']}" if row else f"{ASSESSMENT}:{owner.id}"

    def _append(self, aggregate_type: str, aggregate_id: str, event_type: str, actor: str,
                envelope: dict[str, Any], columns: dict[str, Any],
                publish: bool = True) -> dict[str, Any]:
        """Write one row to the shared log, and pair it with an outbox row.

        Sequence numbers are per aggregate and computed inside the caller's
        transaction, which `BEGIN IMMEDIATE` serializes. The outbox row is
        written here rather than by the caller so that an event can never be
        recorded without its outbound copy.
        """
        event_id = str(columns.pop("event_id", None) or uuid.uuid4())
        sequence = self.db.execute(
            """SELECT COALESCE(MAX(sequence_number),0)+1 FROM event_log
               WHERE aggregate_type=? AND aggregate_id=?""",
            (aggregate_type, str(aggregate_id)),
        ).fetchone()[0]
        row = {
            "event_id": event_id, "aggregate_type": aggregate_type,
            "aggregate_id": str(aggregate_id), "sequence_number": sequence,
            "event_type": event_type, "actor": actor, **columns,
        }
        names = ",".join(row)
        self.db.execute(
            f"INSERT INTO event_log({names}) VALUES ({','.join('?' * len(row))})",
            tuple(row.values()),
        )
        envelope = {"schema_version": EVENT_SCHEMA_VERSION, "event_id": event_id,
                    "event_type": event_type, "aggregate_type": aggregate_type,
                    "aggregate_id": str(aggregate_id), "sequence_number": sequence,
                    "actor": actor, **envelope}
        if publish:
            self.db.execute(
                """INSERT INTO outbox_event
                   (event_id,event_type,payload_json,next_attempt_at,
                    aggregate_type,aggregate_id,aggregate_version,correlation_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (event_id, event_type, json.dumps(envelope), utcnow(), aggregate_type,
                 str(aggregate_id), columns.get("new_revision"), columns.get("correlation_id")),
            )
        return {"event_id": event_id, "sequence_number": sequence}

    def append_event(self, owner: Any, event_type: str, actor: str,
                     step_id: int | None = None, previous: str | None = None,
                     new: str | None = None, payload: dict[str, Any] | None = None,
                     actor_org_id: str | None = None) -> None:
        """An execution event, on the stream of the aggregate it drove.

        There is no separate aggregate type for execution. A request's own
        domain events and the orchestration events that moved it sit in one
        ordered stream, which is what makes a determination and the transition
        it caused readable side by side.
        """
        owner = as_owner(owner)
        correlation_id = self._correlation(owner)
        self._append(
            owner.type, owner.id, event_type, actor,
            envelope={
                "step_instance_id": step_id, "actor_org_id": actor_org_id,
                "previous_state": previous, "new_state": new,
                "correlation_id": correlation_id, "payload": payload or {},
            },
            columns={
                "step_instance_id": step_id, "actor_org_id": actor_org_id,
                "previous_state": previous, "new_state": new,
                "correlation_id": correlation_id,
                "payload_json": json.dumps(payload or {}),
            },
        )

    def append_domain_event(self, aggregate_type: str, aggregate_id: str, event_type: str,
                            actor: str, **fields: Any) -> dict[str, Any]:
        """A domain event, in the same log and the same transaction as execution.

        Written against the same aggregate the orchestration drives, so there
        is one ordering, one gap-detection rule and one outbox, rather than a
        second set of reliability tables beside the first.
        """
        payload = fields.pop("payload", None) or {}
        publish = fields.pop("publish", True)
        changed = fields.pop("changed_fields", None)
        columns = {key: value for key, value in {
            "event_id": fields.pop("event_id", None),
            "actor_org_id": fields.pop("actor_org_id", None),
            "actor_role": fields.pop("actor_role", None),
            "correlation_id": fields.pop("correlation_id", None),
            "command_id": fields.pop("command_id", None),
            "previous_revision": fields.pop("previous_revision", None),
            "new_revision": fields.pop("new_revision", None),
            "reason_reference": fields.pop("reason_reference", None),
            "source_channel": fields.pop("source_channel", None),
            "step_instance_id": fields.pop("step_instance_id", None),
            "changed_fields_json": json.dumps(changed) if changed is not None else None,
            "payload_json": json.dumps(payload),
        }.items() if value is not None}
        if fields:
            raise ValidationError(f"Unknown event fields: {', '.join(sorted(fields))}")
        envelope = {"payload": payload, "correlation_id": columns.get("correlation_id")}
        return self._append(aggregate_type, aggregate_id, event_type, actor,
                            envelope, columns, publish=publish)

    def list_events(self, aggregate_type: str, aggregate_id: str,
                    limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        bounded_limit(limit)
        valid_offset(offset)
        with self.transaction():
            return [decode(row) for row in self.db.execute(
                """SELECT * FROM event_log WHERE aggregate_type=? AND aggregate_id=?
                   ORDER BY sequence_number LIMIT ? OFFSET ?""",
                (aggregate_type, str(aggregate_id), limit, offset))]

    def record_inbox(self, event: dict[str, Any]) -> dict[str, Any]:
        """Durably receive a provider event, deduplicated per connector.

        Returns the stored row and whether it had been seen before, so a host
        connector can decide without a second query.
        """
        existing = self.inbox_event(event["connector_name"], event["provider_event_id"])
        if existing:
            return {"inbox_event": existing, "duplicate": True}
        inbox_id = self.db.execute(
            """INSERT INTO inbox_event
               (connector_name,provider_event_id,event_type,correlation_key,correlation_id,
                payload_json,next_attempt_at)
               VALUES (?,?,?,?,?,?,?)""",
            (event["connector_name"], event["provider_event_id"], event["event_type"],
             event.get("correlation_key"), event.get("correlation_id"),
             json.dumps(event.get("payload", {})), utcnow()),
        ).lastrowid
        return {"inbox_event": decode(self.db.execute(
            "SELECT * FROM inbox_event WHERE id=?", (inbox_id,)).fetchone()),
            "duplicate": False}

    def claim_inbox_events(self, worker_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Lease provider events so only one worker can process each receipt."""
        valid_worker_id(worker_id)
        bounded_limit(limit)
        with self.transaction():
            now = utcnow()
            stale = timestamp(datetime.now(timezone.utc) - timedelta(minutes=5))
            rows = list(self.db.execute(
                """SELECT id FROM inbox_event
                   WHERE (status='RECEIVED' AND (next_attempt_at IS NULL OR next_attempt_at<=?))
                      OR (status='PROCESSING' AND claimed_at<?)
                   ORDER BY id LIMIT ?""", (now, stale, limit)))
            claimed = []
            for row in rows:
                changed = self.db.execute(
                    """UPDATE inbox_event SET status='PROCESSING',claimed_by=?,claimed_at=?
                       WHERE id=? AND ((status='RECEIVED' AND
                           (next_attempt_at IS NULL OR next_attempt_at<=?))
                           OR (status='PROCESSING' AND claimed_at<?))""",
                    (worker_id, now, row["id"], now, stale))
                if changed.rowcount == 1:
                    claimed.append(decode(self.db.execute(
                        "SELECT * FROM inbox_event WHERE id=?", (row["id"],)).fetchone()))
            return claimed

    def list_work(self, assignee: str | None = None, actor: dict[str, Any] | None = None,
                  execution_status: str | None = None, step_type: str | None = None,
                  owner_type: str | None = None, owner_id: int | None = None,
                  limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """Open work, filtered and paginated.

        Without this a client had to fetch whole aggregates and sift them,
        which is why nobody could be shown their own queue.

        `assignee` matches an open assignment. `actor` matches the candidate
        rules instead, answering 'what could this person claim' rather than
        'what is already theirs'.
        """
        bounded_limit(limit)
        valid_offset(offset)
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
                              ("si.owner_type", owner_type),
                              ("si.owner_id", owner_id)):
            if value is not None:
                clauses.append(f"{column}=?")
                args.append(value)
        where = " AND ".join(clauses)
        # Two owner tables, so an outer join to each and a coalesce rather than
        # one join. The owner columns on step_instance decide which side is
        # populated, so exactly one of the two ever matches.
        source = """FROM step_instance si
                    JOIN step_definition sd ON sd.id=si.step_definition_id
                    LEFT JOIN isrp_request rq
                      ON si.owner_type='ISRP_REQUEST' AND rq.id=si.owner_id
                    LEFT JOIN isrp_assessment asmt
                      ON si.owner_type='ISRP_ASSESSMENT' AND asmt.id=si.owner_id"""
        with self.transaction():
            total = int(self.db.execute(
                f"SELECT COUNT(*) {source} WHERE {where}", args).fetchone()[0])
            rows = [decode(row) for row in self.db.execute(
                f"""SELECT si.id step_instance_id,si.state,si.execution_status,
                           si.iteration_number,si.activated_at,si.started_at,
                           si.owner_type,si.owner_id,
                           sd.step_key,sd.name,sd.step_type,sd.stage,sd.assignment_role,
                           sd.execution_mode,
                           COALESCE(rq.title,asmt.title) title,
                           COALESCE(rq.revision,asmt.revision) owner_revision,
                           COALESCE(rq.lifecycle_status,asmt.lifecycle_status) lifecycle_status
                    {source} WHERE {where}
                    ORDER BY si.activated_at,si.id LIMIT ? OFFSET ?""",
                (*args, limit, offset))]
            for row in rows:
                row["assignments"] = [decode(item) for item in self.db.execute(
                    """SELECT assignee_type,assignee,organization_id,status
                       FROM work_assignment WHERE step_instance_id=? AND status='OPEN'""",
                    (row["step_instance_id"],))]
        return {"items": rows, "total": total, "limit": limit, "offset": offset}

    def stuck_aggregates(self, limit: int = 50) -> list[dict[str, Any]]:
        """Running requests and assessments with nothing that can advance them.

        No node ready, active or waiting; no job queued or running; no timer
        scheduled. Such a run is not slow, it is wedged, and nothing else in
        the system will say so. The limit is applied per owner type and then to
        the combined result, so a flood of one kind cannot hide the other.
        """
        bounded_limit(limit)
        with self.transaction():
            found: list[dict[str, Any]] = []
            for owner_type, table in OWNER_TABLES.items():
                found.extend(dict(decode(row), owner_type=owner_type) for row in self.db.execute(
                    f"""SELECT w.id,w.title,w.current_stage,w.lifecycle_status,w.created_at
                        FROM {table} w
                        WHERE w.execution_status='RUNNING'
                          AND NOT EXISTS (SELECT 1 FROM step_instance s
                              WHERE s.owner_type=? AND s.owner_id=w.id
                                AND s.execution_status IN ('READY','ACTIVE','WAITING'))
                          AND NOT EXISTS (SELECT 1 FROM automation_job j
                              WHERE j.owner_type=? AND j.owner_id=w.id
                                AND j.status IN ('QUEUED','RETRY_WAIT','RUNNING'))
                          AND NOT EXISTS (SELECT 1 FROM durable_timer t
                              WHERE t.owner_type=? AND t.owner_id=w.id AND t.status='SCHEDULED')
                        ORDER BY w.id LIMIT ?""",
                    (owner_type, owner_type, owner_type, limit)))
            return found[:limit]

    def dead_letter_events(self, limit: int = 50) -> list[dict[str, Any]]:
        bounded_limit(limit)
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

            def by_execution(status: str) -> int:
                return sum(scalar(f"SELECT COUNT(*) FROM {table} WHERE execution_status=?",
                                  status) for table in OWNER_TABLES.values())

            return {
                "aggregates_running": by_execution("RUNNING"),
                "aggregates_suspended": by_execution("SUSPENDED"),
                "aggregates_failed": by_execution("FAILED"),
                "aggregates_stuck": len(self.stuck_aggregates(limit=1000)),
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
        valid_worker_id(worker_id)
        bounded_limit(limit)
        with self.transaction():
            now = utcnow()
            stale = timestamp(datetime.now(timezone.utc) - timedelta(minutes=5))
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
                next_at = timestamp(datetime.now(timezone.utc) + timedelta(seconds=delay))
                self.db.execute(
                    """UPDATE outbox_event SET status='PENDING',attempts=?,last_error=?,next_attempt_at=?,
                       claimed_by=NULL,claimed_at=NULL WHERE id=?""",
                    (attempts, error, next_at, outbox_id),
                )


@contextmanager
def _null_context():
    yield
