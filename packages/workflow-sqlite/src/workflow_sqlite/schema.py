SCHEMA = """
CREATE TABLE IF NOT EXISTS fsm_definition (
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  scope TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS fsm_version (
  id INTEGER PRIMARY KEY AUTOINCREMENT, definition_id INTEGER NOT NULL REFERENCES fsm_definition(id),
  version_number INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT', initial_state TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, published_at TEXT,
  UNIQUE(definition_id, version_number)
);
CREATE TABLE IF NOT EXISTS fsm_state_definition (
  id INTEGER PRIMARY KEY AUTOINCREMENT, fsm_version_id INTEGER NOT NULL REFERENCES fsm_version(id),
  state_key TEXT NOT NULL, terminal INTEGER NOT NULL DEFAULT 0,
  UNIQUE(fsm_version_id, state_key)
);
CREATE TABLE IF NOT EXISTS fsm_transition_definition (
  id INTEGER PRIMARY KEY AUTOINCREMENT, fsm_version_id INTEGER NOT NULL REFERENCES fsm_version(id),
  action TEXT NOT NULL, from_state TEXT NOT NULL, to_state TEXT NOT NULL,
  guard_json TEXT, required_permission TEXT, reason_required INTEGER NOT NULL DEFAULT 0,
  UNIQUE(fsm_version_id, from_state, action)
);
CREATE TABLE IF NOT EXISTS workflow_definition (
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '', domain TEXT NOT NULL DEFAULT 'GENERIC',
  status TEXT NOT NULL DEFAULT 'ACTIVE', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS workflow_version (
  id INTEGER PRIMARY KEY AUTOINCREMENT, definition_id INTEGER NOT NULL REFERENCES workflow_definition(id),
  version_number INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT',
  lifecycle_fsm_version_id INTEGER REFERENCES fsm_version(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, published_at TEXT,
  UNIQUE(definition_id, version_number)
);
CREATE TABLE IF NOT EXISTS step_definition (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_version_id INTEGER NOT NULL REFERENCES workflow_version(id),
  step_key TEXT NOT NULL, name TEXT NOT NULL, step_type TEXT NOT NULL, stage TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '', assignment_role TEXT, join_rule TEXT,
  step_fsm_version_id INTEGER REFERENCES fsm_version(id),
  configuration_json TEXT NOT NULL DEFAULT '{}', UNIQUE(workflow_version_id, step_key)
);
CREATE TABLE IF NOT EXISTS transition_definition (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_version_id INTEGER NOT NULL REFERENCES workflow_version(id),
  from_step_id INTEGER NOT NULL REFERENCES step_definition(id), to_step_id INTEGER NOT NULL REFERENCES step_definition(id),
  condition_json TEXT, priority INTEGER NOT NULL DEFAULT 100
);
CREATE TABLE IF NOT EXISTS workflow_instance (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_version_id INTEGER NOT NULL REFERENCES workflow_version(id),
  title TEXT NOT NULL, business_type TEXT, business_key TEXT, correlation_id TEXT,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  lifecycle_state TEXT NOT NULL, execution_status TEXT NOT NULL DEFAULT 'RUNNING',
  current_stage TEXT, revision INTEGER NOT NULL DEFAULT 0, variables_json TEXT NOT NULL DEFAULT '{}',
  parent_workflow_instance_id INTEGER REFERENCES workflow_instance(id),
  root_workflow_instance_id INTEGER REFERENCES workflow_instance(id),
  parent_step_instance_id INTEGER, relationship_type TEXT, relationship_key TEXT,
  required_flag INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT,
  suspended_at TEXT, cancelled_at TEXT
);
CREATE TABLE IF NOT EXISTS workflow_subject (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_instance_id INTEGER NOT NULL REFERENCES workflow_instance(id),
  subject_type TEXT NOT NULL, subject_id TEXT NOT NULL, source_system TEXT NOT NULL,
  relationship TEXT NOT NULL DEFAULT 'PRIMARY'
);
CREATE TABLE IF NOT EXISTS workflow_fact_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_instance_id INTEGER NOT NULL REFERENCES workflow_instance(id),
  fact_key TEXT NOT NULL, previous_value_json TEXT, new_value_json TEXT, source_type TEXT NOT NULL,
  source_reference TEXT, actor TEXT NOT NULL, revision INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS step_instance (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_instance_id INTEGER NOT NULL REFERENCES workflow_instance(id),
  step_definition_id INTEGER NOT NULL REFERENCES step_definition(id), iteration_number INTEGER NOT NULL DEFAULT 1,
  state TEXT NOT NULL, execution_status TEXT NOT NULL DEFAULT 'NOT_READY', result_json TEXT,
  activated_at TEXT, started_at TEXT, completed_at TEXT,
  UNIQUE(workflow_instance_id, step_definition_id)
);
CREATE TABLE IF NOT EXISTS step_attempt (
  id INTEGER PRIMARY KEY AUTOINCREMENT, step_instance_id INTEGER NOT NULL REFERENCES step_instance(id),
  iteration_number INTEGER NOT NULL, started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  submitted_at TEXT, completed_at TEXT, outcome TEXT, result_json TEXT,
  UNIQUE(step_instance_id, iteration_number)
);
CREATE TABLE IF NOT EXISTS work_assignment (
  id INTEGER PRIMARY KEY AUTOINCREMENT, step_instance_id INTEGER NOT NULL REFERENCES step_instance(id),
  assignee_type TEXT NOT NULL DEFAULT 'ROLE', assignee TEXT NOT NULL,
  organization_id TEXT, assigned_by TEXT, reason TEXT,
  mandatory INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'OPEN', due_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, ended_at TEXT
);
CREATE TABLE IF NOT EXISTS work_candidate (
  id INTEGER PRIMARY KEY AUTOINCREMENT, step_instance_id INTEGER NOT NULL REFERENCES step_instance(id),
  candidate_type TEXT NOT NULL, candidate_value TEXT NOT NULL, organization_id TEXT,
  UNIQUE(step_instance_id, candidate_type, candidate_value, organization_id)
);
CREATE TABLE IF NOT EXISTS signal_receipt (
  id INTEGER PRIMARY KEY AUTOINCREMENT, command_id TEXT NOT NULL UNIQUE,
  workflow_instance_id INTEGER NOT NULL REFERENCES workflow_instance(id),
  signal_type TEXT NOT NULL, correlation_key TEXT, payload_json TEXT NOT NULL DEFAULT '{}',
  received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, consumed_at TEXT,
  consumed_step_instance_id INTEGER REFERENCES step_instance(id)
);
CREATE TABLE IF NOT EXISTS automation_job (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT NOT NULL UNIQUE,
  workflow_instance_id INTEGER NOT NULL REFERENCES workflow_instance(id),
  step_instance_id INTEGER NOT NULL REFERENCES step_instance(id),
  handler TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'QUEUED', attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3, input_json TEXT NOT NULL DEFAULT '{}', result_json TEXT,
  available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, claimed_by TEXT, claimed_at TEXT,
  lease_expires_at TEXT, completed_at TEXT, last_error TEXT
);
CREATE TABLE IF NOT EXISTS durable_timer (
  id INTEGER PRIMARY KEY AUTOINCREMENT, timer_key TEXT NOT NULL UNIQUE,
  workflow_instance_id INTEGER NOT NULL REFERENCES workflow_instance(id),
  step_instance_id INTEGER REFERENCES step_instance(id), timer_type TEXT NOT NULL,
  action TEXT NOT NULL, due_at TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'SCHEDULED', fired_at TEXT, cancelled_at TEXT
);
CREATE TABLE IF NOT EXISTS workflow_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
  workflow_instance_id INTEGER NOT NULL REFERENCES workflow_instance(id),
  sequence_number INTEGER NOT NULL, step_instance_id INTEGER REFERENCES step_instance(id),
  event_type TEXT NOT NULL, actor TEXT NOT NULL, actor_org_id TEXT,
  previous_state TEXT, new_state TEXT, payload_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(workflow_instance_id, sequence_number)
);
CREATE TABLE IF NOT EXISTS workflow_command (
  command_id TEXT PRIMARY KEY, workflow_instance_id INTEGER NOT NULL REFERENCES workflow_instance(id),
  action TEXT NOT NULL, result_json TEXT NOT NULL, processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inbox_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT, connector_name TEXT NOT NULL, provider_event_id TEXT NOT NULL,
  event_type TEXT NOT NULL, correlation_key TEXT, payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'RECEIVED', attempts INTEGER NOT NULL DEFAULT 0,
  received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, processed_at TEXT, last_error TEXT,
  UNIQUE(connector_name,provider_event_id)
);
CREATE TABLE IF NOT EXISTS outbox_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 10,
  next_attempt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, claimed_by TEXT, claimed_at TEXT,
  last_error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, processed_at TEXT
);
CREATE TABLE IF NOT EXISTS webhook_subscription (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, target_url TEXT NOT NULL,
  event_types_json TEXT NOT NULL DEFAULT '[]', secret TEXT, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS outbox_delivery (
  id INTEGER PRIMARY KEY AUTOINCREMENT, outbox_event_id INTEGER NOT NULL REFERENCES outbox_event(id),
  subscription_id INTEGER NOT NULL REFERENCES webhook_subscription(id), status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, delivered_at TEXT,
  UNIQUE(outbox_event_id, subscription_id)
);
CREATE TABLE IF NOT EXISTS schema_metadata (
  key TEXT PRIMARY KEY, value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# Created only after the legacy migration, which adds the columns some of
# these indexes reference.
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_workflow_business ON workflow_instance(business_type, business_key);
CREATE INDEX IF NOT EXISTS idx_workflow_correlation ON workflow_instance(correlation_id);
CREATE INDEX IF NOT EXISTS idx_signal_match ON signal_receipt(workflow_instance_id,signal_type,correlation_key,consumed_at);
CREATE INDEX IF NOT EXISTS idx_automation_claim ON automation_job(status,available_at,id);
CREATE INDEX IF NOT EXISTS idx_timer_due ON durable_timer(status,due_at,id);
CREATE INDEX IF NOT EXISTS idx_step_instance_workflow ON step_instance(workflow_instance_id);
CREATE INDEX IF NOT EXISTS idx_event_workflow ON workflow_event(workflow_instance_id, sequence_number);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox_event(status,next_attempt_at,id);
CREATE INDEX IF NOT EXISTS idx_workflow_parent ON workflow_instance(parent_workflow_instance_id);
"""

# Stamped into schema_metadata on first initialization. Its presence is what
# tells initialize() that the legacy 0.2 migration has already been applied and
# must never run against live rows again.
SCHEMA_VERSION = 3
