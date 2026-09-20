SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_definition (
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '', domain TEXT NOT NULL DEFAULT 'GENERIC',
  status TEXT NOT NULL DEFAULT 'ACTIVE', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS workflow_version (
  id INTEGER PRIMARY KEY AUTOINCREMENT, definition_id INTEGER NOT NULL REFERENCES workflow_definition(id),
  version_number INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, published_at TEXT,
  UNIQUE(definition_id, version_number)
);
CREATE TABLE IF NOT EXISTS step_definition (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_version_id INTEGER NOT NULL REFERENCES workflow_version(id),
  step_key TEXT NOT NULL, name TEXT NOT NULL, step_type TEXT NOT NULL, stage TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '', assignment_role TEXT, join_rule TEXT,
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
  status TEXT NOT NULL DEFAULT 'ACTIVE', current_stage TEXT, revision INTEGER NOT NULL DEFAULT 0,
  variables_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_workflow_business ON workflow_instance(business_type, business_key);
CREATE INDEX IF NOT EXISTS idx_workflow_correlation ON workflow_instance(correlation_id);
CREATE TABLE IF NOT EXISTS workflow_subject (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_instance_id INTEGER NOT NULL REFERENCES workflow_instance(id),
  subject_type TEXT NOT NULL, subject_id TEXT NOT NULL, source_system TEXT NOT NULL,
  relationship TEXT NOT NULL DEFAULT 'PRIMARY'
);
CREATE TABLE IF NOT EXISTS step_instance (
  id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_instance_id INTEGER NOT NULL REFERENCES workflow_instance(id),
  step_definition_id INTEGER NOT NULL REFERENCES step_definition(id), iteration_number INTEGER NOT NULL DEFAULT 1,
  state TEXT NOT NULL DEFAULT 'NOT_READY', result_json TEXT, activated_at TEXT, started_at TEXT, completed_at TEXT,
  UNIQUE(workflow_instance_id, step_definition_id, iteration_number)
);
CREATE TABLE IF NOT EXISTS work_assignment (
  id INTEGER PRIMARY KEY AUTOINCREMENT, step_instance_id INTEGER NOT NULL REFERENCES step_instance(id),
  assignee_type TEXT NOT NULL DEFAULT 'ROLE', assignee TEXT NOT NULL, mandatory INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'OPEN', due_at TEXT
);
CREATE TABLE IF NOT EXISTS workflow_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
  workflow_instance_id INTEGER NOT NULL REFERENCES workflow_instance(id),
  sequence_number INTEGER NOT NULL, step_instance_id INTEGER REFERENCES step_instance(id),
  event_type TEXT NOT NULL, actor TEXT NOT NULL, previous_state TEXT, new_state TEXT,
  payload_json TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(workflow_instance_id, sequence_number)
);
CREATE TABLE IF NOT EXISTS workflow_command (
  command_id TEXT PRIMARY KEY, workflow_instance_id INTEGER NOT NULL REFERENCES workflow_instance(id),
  action TEXT NOT NULL, result_json TEXT NOT NULL, processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS outbox_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
  attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  processed_at TEXT
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
CREATE INDEX IF NOT EXISTS idx_step_instance_workflow ON step_instance(workflow_instance_id);
CREATE INDEX IF NOT EXISTS idx_event_workflow ON workflow_event(workflow_instance_id, sequence_number);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox_event(status, id);
"""
