"""The one schema: ISRP's aggregates, the orchestration that drives them, and
the reliability tables both share.

There is no `workflow_instance`. An ISRP request and an ISRP assessment are the
two things this application orchestrates, so they are the two tables that carry
orchestration state, and every runtime row points at one of them through
`(owner_type, owner_id)`. Only the orchestration columns live here; the business
columns of a request and an assessment belong to the ISRP data model and are
added as that is built out.
"""

from __future__ import annotations

# One statement per table, keyed by table name, so that a migration which has
# to rebuild a table can re-issue exactly that table's definition rather than
# keeping a second copy of it in the migration.
TABLES: dict[str, str] = {
    "fsm_definition": """
CREATE TABLE IF NOT EXISTS fsm_definition (
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  scope TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);""",
    "fsm_version": """
CREATE TABLE IF NOT EXISTS fsm_version (
  id INTEGER PRIMARY KEY AUTOINCREMENT, definition_id INTEGER NOT NULL REFERENCES fsm_definition(id),
  version_number INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT', initial_state TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), published_at TEXT,
  UNIQUE(definition_id, version_number)
);""",
    "fsm_state_definition": """
CREATE TABLE IF NOT EXISTS fsm_state_definition (
  id INTEGER PRIMARY KEY AUTOINCREMENT, fsm_version_id INTEGER NOT NULL REFERENCES fsm_version(id),
  state_key TEXT NOT NULL, terminal INTEGER NOT NULL DEFAULT 0, category TEXT,
  UNIQUE(fsm_version_id, state_key)
);""",
    "fsm_transition_definition": """
CREATE TABLE IF NOT EXISTS fsm_transition_definition (
  id INTEGER PRIMARY KEY AUTOINCREMENT, fsm_version_id INTEGER NOT NULL REFERENCES fsm_version(id),
  action TEXT NOT NULL, from_state TEXT NOT NULL, to_state TEXT NOT NULL,
  guard_json TEXT, required_permission TEXT, reason_required INTEGER NOT NULL DEFAULT 0,
  UNIQUE(fsm_version_id, from_state, action)
);""",
    "workflow_definition": """
CREATE TABLE IF NOT EXISTS workflow_definition (
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '', domain TEXT NOT NULL DEFAULT 'GENERIC',
  status TEXT NOT NULL DEFAULT 'ACTIVE', created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);""",
    "workflow_version": """
CREATE TABLE IF NOT EXISTS workflow_version (
  id INTEGER PRIMARY KEY AUTOINCREMENT, definition_id INTEGER NOT NULL REFERENCES workflow_definition(id),
  version_number INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT',
  lifecycle_fsm_version_id INTEGER REFERENCES fsm_version(id),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), published_at TEXT,
  UNIQUE(definition_id, version_number)
);""",
    "step_definition": """
CREATE TABLE IF NOT EXISTS step_definition (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_version_id INTEGER NOT NULL REFERENCES workflow_version(id),
  step_key TEXT NOT NULL, name TEXT NOT NULL, step_type TEXT NOT NULL, stage TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '', assignment_role TEXT, join_rule TEXT, execution_mode TEXT,
  step_fsm_version_id INTEGER REFERENCES fsm_version(id),
  configuration_json TEXT NOT NULL DEFAULT '{}', UNIQUE(workflow_version_id, step_key)
);""",
    "transition_definition": """
CREATE TABLE IF NOT EXISTS transition_definition (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_version_id INTEGER NOT NULL REFERENCES workflow_version(id),
  from_step_id INTEGER NOT NULL REFERENCES step_definition(id), to_step_id INTEGER NOT NULL REFERENCES step_definition(id),
  condition_json TEXT, priority INTEGER NOT NULL DEFAULT 100
);""",
    # The two orchestrated aggregates. Orchestration columns only: the business
    # columns named in the ISRP data model are added beside these as ISRP is
    # built, on the same rows, so that a request and its orchestration state
    # commit together and there is one revision to lock against.
    "isrp_request": """
CREATE TABLE IF NOT EXISTS isrp_request (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workflow_version_id INTEGER NOT NULL REFERENCES workflow_version(id),
  title TEXT NOT NULL,
  lifecycle_status TEXT NOT NULL, execution_status TEXT NOT NULL DEFAULT 'RUNNING',
  current_stage TEXT, revision INTEGER NOT NULL DEFAULT 0,
  variables_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  completed_at TEXT, suspended_at TEXT, cancelled_at TEXT
);""",
    # An assessment belongs to exactly one request. That foreign key is the
    # whole of the parent-child machinery the engine used to carry in opaque
    # columns: there is no relationship type, no relationship key and no root
    # pointer, because there are exactly two levels and the second one is this.
    "isrp_assessment": """
CREATE TABLE IF NOT EXISTS isrp_assessment (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id INTEGER NOT NULL REFERENCES isrp_request(id),
  assessment_type TEXT,
  parent_step_instance_id INTEGER REFERENCES step_instance(id),
  required_flag INTEGER NOT NULL DEFAULT 1,
  workflow_version_id INTEGER NOT NULL REFERENCES workflow_version(id),
  title TEXT NOT NULL,
  lifecycle_status TEXT NOT NULL, execution_status TEXT NOT NULL DEFAULT 'RUNNING',
  current_stage TEXT, revision INTEGER NOT NULL DEFAULT 0,
  variables_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  completed_at TEXT, suspended_at TEXT, cancelled_at TEXT
);""",
    "workflow_fact_history": """
CREATE TABLE IF NOT EXISTS workflow_fact_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_type TEXT NOT NULL, owner_id INTEGER NOT NULL,
  fact_key TEXT NOT NULL, previous_value_json TEXT, new_value_json TEXT, source_type TEXT NOT NULL,
  source_reference TEXT, actor TEXT NOT NULL, revision INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);""",
    "step_instance": """
CREATE TABLE IF NOT EXISTS step_instance (
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_type TEXT NOT NULL, owner_id INTEGER NOT NULL,
  step_definition_id INTEGER NOT NULL REFERENCES step_definition(id), iteration_number INTEGER NOT NULL DEFAULT 1,
  state TEXT NOT NULL, execution_status TEXT NOT NULL DEFAULT 'NOT_READY', result_json TEXT,
  activated_at TEXT, started_at TEXT, completed_at TEXT,
  UNIQUE(owner_type, owner_id, step_definition_id)
);""",
    "step_attempt": """
CREATE TABLE IF NOT EXISTS step_attempt (
  id INTEGER PRIMARY KEY AUTOINCREMENT, step_instance_id INTEGER NOT NULL REFERENCES step_instance(id),
  iteration_number INTEGER NOT NULL, started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  submitted_at TEXT, completed_at TEXT, outcome TEXT, result_json TEXT,
  UNIQUE(step_instance_id, iteration_number)
);""",
    "work_assignment": """
CREATE TABLE IF NOT EXISTS work_assignment (
  id INTEGER PRIMARY KEY AUTOINCREMENT, step_instance_id INTEGER NOT NULL REFERENCES step_instance(id),
  assignee_type TEXT NOT NULL DEFAULT 'ROLE', assignee TEXT NOT NULL,
  organization_id TEXT, assigned_by TEXT, reason TEXT,
  mandatory INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'OPEN', due_at TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), ended_at TEXT
);""",
    "work_candidate": """
CREATE TABLE IF NOT EXISTS work_candidate (
  id INTEGER PRIMARY KEY AUTOINCREMENT, step_instance_id INTEGER NOT NULL REFERENCES step_instance(id),
  candidate_type TEXT NOT NULL, candidate_value TEXT NOT NULL, organization_id TEXT,
  UNIQUE(step_instance_id, candidate_type, candidate_value, organization_id)
);""",
    "signal_receipt": """
CREATE TABLE IF NOT EXISTS signal_receipt (
  id INTEGER PRIMARY KEY AUTOINCREMENT, command_id TEXT NOT NULL UNIQUE,
  owner_type TEXT NOT NULL, owner_id INTEGER NOT NULL,
  signal_type TEXT NOT NULL, correlation_key TEXT, payload_json TEXT NOT NULL DEFAULT '{}',
  received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), consumed_at TEXT,
  consumed_step_instance_id INTEGER REFERENCES step_instance(id)
);""",
    "automation_job": """
CREATE TABLE IF NOT EXISTS automation_job (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT NOT NULL UNIQUE,
  owner_type TEXT NOT NULL, owner_id INTEGER NOT NULL,
  step_instance_id INTEGER NOT NULL REFERENCES step_instance(id),
  handler TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'QUEUED', attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3, input_json TEXT NOT NULL DEFAULT '{}', result_json TEXT,
  available_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), claimed_by TEXT, claimed_at TEXT,
  lease_expires_at TEXT, completed_at TEXT, last_error TEXT
);""",
    "durable_timer": """
CREATE TABLE IF NOT EXISTS durable_timer (
  id INTEGER PRIMARY KEY AUTOINCREMENT, timer_key TEXT NOT NULL UNIQUE,
  owner_type TEXT NOT NULL, owner_id INTEGER NOT NULL,
  step_instance_id INTEGER REFERENCES step_instance(id), timer_type TEXT NOT NULL,
  action TEXT NOT NULL, due_at TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'SCHEDULED', fired_at TEXT, cancelled_at TEXT
);""",
    # One ordered log for the whole application. Orchestration writes against
    # the aggregate it is driving -- ISRP_REQUEST or ISRP_ASSESSMENT -- and
    # ISRP writes its own domain events against the same aggregate, so a
    # determination and the step transition it caused sit next to each other in
    # one stream. Sequence numbers are dense per aggregate, so any consumer can
    # detect a gap.
    "event_log": """
CREATE TABLE IF NOT EXISTS event_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
  aggregate_type TEXT NOT NULL, aggregate_id TEXT NOT NULL,
  sequence_number INTEGER NOT NULL,
  step_instance_id INTEGER REFERENCES step_instance(id),
  event_type TEXT NOT NULL, actor TEXT NOT NULL, actor_org_id TEXT, actor_role TEXT,
  correlation_id TEXT, command_id TEXT,
  previous_state TEXT, new_state TEXT,
  previous_revision INTEGER, new_revision INTEGER,
  changed_fields_json TEXT, reason_reference TEXT, source_channel TEXT,
  payload_json TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE(aggregate_type, aggregate_id, sequence_number)
);""",
    # A receipt, not a snapshot: which aggregate the command hit and the
    # revision it produced. Storing the response instead made command storage
    # grow with the square of the number of commands.
    "workflow_command": """
CREATE TABLE IF NOT EXISTS workflow_command (
  command_id TEXT PRIMARY KEY, owner_type TEXT NOT NULL, owner_id INTEGER NOT NULL,
  action TEXT NOT NULL, revision INTEGER, request_fingerprint TEXT,
  processed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);""",
    "inbox_event": """
CREATE TABLE IF NOT EXISTS inbox_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT, connector_name TEXT NOT NULL, provider_event_id TEXT NOT NULL,
  event_type TEXT NOT NULL, correlation_key TEXT, payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'RECEIVED', attempts INTEGER NOT NULL DEFAULT 0,
  received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), processed_at TEXT, last_error TEXT,
  correlation_id TEXT, next_attempt_at TEXT, claimed_by TEXT, claimed_at TEXT,
  UNIQUE(connector_name,provider_event_id)
);""",
    "outbox_event": """
CREATE TABLE IF NOT EXISTS outbox_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 10,
  next_attempt_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), claimed_by TEXT, claimed_at TEXT,
  last_error TEXT, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), processed_at TEXT,
  aggregate_type TEXT, aggregate_id TEXT, aggregate_version INTEGER, correlation_id TEXT
);""",
    "webhook_subscription": """
CREATE TABLE IF NOT EXISTS webhook_subscription (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, target_url TEXT NOT NULL,
  event_types_json TEXT NOT NULL DEFAULT '[]', secret TEXT, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);""",
    "outbox_delivery": """
CREATE TABLE IF NOT EXISTS outbox_delivery (
  id INTEGER PRIMARY KEY AUTOINCREMENT, outbox_event_id INTEGER NOT NULL REFERENCES outbox_event(id),
  subscription_id INTEGER NOT NULL REFERENCES webhook_subscription(id), status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, delivered_at TEXT,
  UNIQUE(outbox_event_id, subscription_id)
);""",
    "schema_metadata": """
CREATE TABLE IF NOT EXISTS schema_metadata (
  key TEXT PRIMARY KEY, value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);""",
}

SCHEMA = "\n".join(TABLES.values())

# Created only after the migration chain, which adds the columns some of these
# indexes reference and rebuilds the tables others sit on.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_signal_match ON signal_receipt(owner_type,owner_id,signal_type,correlation_key,consumed_at);
CREATE INDEX IF NOT EXISTS idx_automation_claim ON automation_job(status,available_at,id);
CREATE INDEX IF NOT EXISTS idx_timer_due ON durable_timer(status,due_at,id);
CREATE INDEX IF NOT EXISTS idx_step_instance_owner ON step_instance(owner_type,owner_id);
CREATE INDEX IF NOT EXISTS idx_event_aggregate ON event_log(aggregate_type, aggregate_id, sequence_number);
CREATE INDEX IF NOT EXISTS idx_inbox_claim ON inbox_event(status, next_attempt_at, id);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox_event(status,next_attempt_at,id);
CREATE INDEX IF NOT EXISTS idx_assessment_request ON isrp_assessment(request_id);
CREATE INDEX IF NOT EXISTS idx_assessment_parent_step ON isrp_assessment(parent_step_instance_id);
CREATE INDEX IF NOT EXISTS idx_assignment_open ON work_assignment(assignee,status);
CREATE INDEX IF NOT EXISTS idx_step_execution ON step_instance(execution_status,owner_type,owner_id);
CREATE INDEX IF NOT EXISTS idx_command_owner ON workflow_command(owner_type,owner_id);
CREATE INDEX IF NOT EXISTS idx_request_execution ON isrp_request(execution_status,id);
CREATE INDEX IF NOT EXISTS idx_assessment_execution ON isrp_assessment(execution_status,id);
"""

# Stamped into schema_metadata on first initialization. Its presence is what
# tells initialize() that the legacy 0.2 migration has already been applied and
# must never run against live rows again.
SCHEMA_VERSION = 8

# Applied in order by create_schema for a database stamped below the current
# version. Each entry is (target_version, method name on the repository).
MIGRATIONS = (
    (4, "_migrate_to_4_state_category"),
    (5, "_migrate_to_5_shared_event_log"),
    (6, "_migrate_to_6_execution_mode"),
    (7, "_migrate_to_7_owner_aggregates"),
    (8, "_migrate_to_8_command_fingerprint"),
)

# Stamped onto every outbound message so a consumer can tell which shape it is
# reading. Bump it whenever the event envelope changes.
EVENT_SCHEMA_VERSION = 1
