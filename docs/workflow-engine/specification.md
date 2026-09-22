# Flow Engine Reference

Status: Current. Regenerated against the running schema and API.

This is the reference: entities, fields, vocabularies, configuration keys and
endpoints. It says what exists, not how or why it works.

~~~text
../charter.md             why Flow exists, and what was decided
../requirements.md        what it must do, with a status and a citing test
../technical-design.md    how the mechanisms work, and why they are that shape
this document             the reference: names, fields, values, endpoints
../integration.md         the HTTP contract for a client integrator
~~~

Earlier revisions of this file also carried the architectural narrative. That
moved to the technical design, because two documents describing the same
mechanism drift apart and the reader cannot tell which one is lying. Anything
here that looks like an explanation is a cross-reference instead.

Field lists below are the real columns in the order the schema declares them.
`_json` columns hold JSON encoded by the adapter and are exposed without the
suffix by the repository.

# Part I: Definition entities

Immutable once published. A running instance stays pinned to the version it
started on.

## FSM_DEFINITION

~~~text
id
key                globally unique stable name
name
scope              WORKFLOW | STEP
created_at
~~~

## FSM_VERSION

~~~text
id
definition_id
version_number
status             DRAFT | PUBLISHED | RETIRED
initial_state
created_at
published_at
~~~

Constraint: `UNIQUE(definition_id, version_number)`

## FSM_STATE_DEFINITION

~~~text
id
fsm_version_id
state_key
terminal
category           execution category, or null to let the engine resolve it
~~~

Constraint: `UNIQUE(fsm_version_id, state_key)`

`category` is the bridge between the definition's state and the engine's view of
a node. See Part V for the permitted values and the technical design, section
11, for how an absent one is resolved.

## FSM_TRANSITION_DEFINITION

~~~text
id
fsm_version_id
action
from_state
to_state
guard_json
required_permission
reason_required
~~~

Constraint: `UNIQUE(fsm_version_id, from_state, action)`

## WORKFLOW_DEFINITION

~~~text
id
key
name
description
domain
status
created_at
~~~

Constraint: `UNIQUE(key)`

## WORKFLOW_VERSION

~~~text
id
definition_id
version_number
status                     DRAFT | PUBLISHED
lifecycle_fsm_version_id
created_at
published_at
~~~

Constraint: `UNIQUE(definition_id, version_number)`. Republishing an existing
version number raises `ConflictError`.

## STEP_DEFINITION

~~~text
id
workflow_version_id
step_key
name
step_type
stage
description
assignment_role
join_rule
step_fsm_version_id
configuration_json
~~~

Constraint: `UNIQUE(workflow_version_id, step_key)`. Step key is the identity
that survives a version migration.

## TRANSITION_DEFINITION

~~~text
id
workflow_version_id
from_step_id
to_step_id
condition_json
priority                   stored, not currently used by the engine
~~~

# Part II: Runtime entities

## WORKFLOW_INSTANCE

~~~text
id
workflow_version_id
title
business_type
business_key
correlation_id
status                          compatibility projection of execution_status
lifecycle_state
execution_status
current_stage
revision
variables_json

parent_workflow_instance_id
root_workflow_instance_id
parent_step_instance_id
relationship_type
relationship_key
required_flag

created_by                      the actor's id, whatever shape it arrived in
created_at
completed_at
suspended_at
cancelled_at
~~~

`status` maps `COMPLETED`, `CANCELLED` and `FAILED` through unchanged and
reports everything else as `ACTIVE`. It exists for older readers; new ones
should use `execution_status`.

## WORKFLOW_SUBJECT

~~~text
id
workflow_instance_id
subject_type
subject_id
source_system
relationship
~~~

## WORKFLOW_FACT_HISTORY

~~~text
id
workflow_instance_id
fact_key
previous_value_json
new_value_json
source_type
source_reference
actor
revision
created_at
~~~

Append-only. A fact set to the value it already holds writes no row.

## STEP_INSTANCE

~~~text
id
workflow_instance_id
step_definition_id
iteration_number
state                      from the pinned step FSM
execution_status           the engine's category for that state
result_json
activated_at
started_at
completed_at
~~~

Constraint: `UNIQUE(workflow_instance_id, step_definition_id)`

Skipping and cancellation set `execution_status` and leave `state` alone,
because a custom step FSM need not declare a skipped or cancelled state. A node
can therefore read `state=READY, execution_status=CANCELLED`; render the
category, not the state.

## STEP_ATTEMPT

~~~text
id
step_instance_id
iteration_number
started_at
submitted_at
completed_at
outcome
result_json
~~~

Constraint: `UNIQUE(step_instance_id, iteration_number)`

## WORK_CANDIDATE

~~~text
id
step_instance_id
candidate_type            USER | ROLE | GROUP | ORGANIZATION
candidate_value
organization_id
~~~

Constraint: `UNIQUE(step_instance_id, candidate_type, candidate_value, organization_id)`

## WORK_ASSIGNMENT

~~~text
id
step_instance_id
assignee_type
assignee
organization_id
assigned_by
reason
mandatory
status                    OPEN | REPLACED | CANCELLED
due_at
created_at
ended_at
~~~

Reassignment marks the previous row `REPLACED` and inserts a new one. Nothing is
overwritten.

# Part III: Asynchronous entities

## SIGNAL_RECEIPT

~~~text
id
command_id
workflow_instance_id
signal_type
correlation_key
payload_json
received_at
consumed_at
consumed_step_instance_id
~~~

Constraint: `UNIQUE(command_id)`

## AUTOMATION_JOB

~~~text
id
job_key
workflow_instance_id
step_instance_id
handler
status                    QUEUED | RUNNING | RETRY_WAIT | SUCCEEDED | FAILED | CANCELLED
attempt_count
max_attempts
input_json
result_json
available_at
claimed_by
claimed_at
lease_expires_at
completed_at
last_error
~~~

Constraint: `UNIQUE(job_key)`, where the key is
`{workflow_id}:{step_instance_id}:{iteration_number}`.

## DURABLE_TIMER

~~~text
id
timer_key
workflow_instance_id
step_instance_id
timer_type                RELATIVE
action                    COMPLETE_STEP | SLA_BREACH
due_at
payload_json
status                    SCHEDULED | FIRED | CANCELLED
fired_at
cancelled_at
~~~

Constraint: `UNIQUE(timer_key)`, where the key is
`{workflow_id}:{step_instance_id}:{iteration_number}:{action}`. The action is
part of the key so that one node can carry both a delay timer and a
service-level timer for the same iteration.

# Part IV: Reliability entities

## WORKFLOW_COMMAND

~~~text
command_id                primary key
workflow_instance_id
action
result_json               a receipt, not a snapshot
processed_at
~~~

The receipt is `{"workflow_instance_id", "action", "revision"}`. A repeated
command returns the workflow's current state, read fresh. See the technical
design, section 23, for why.

## WORKFLOW_EVENT

~~~text
id
event_id                  globally unique
workflow_instance_id
sequence_number
step_instance_id
event_type
actor
actor_org_id
previous_state
new_state
payload_json
created_at
~~~

Constraint: `UNIQUE(event_id)`, `UNIQUE(workflow_instance_id, sequence_number)`.
Sequence numbers are dense per workflow, so a consumer can detect a gap.

## INBOX_EVENT

~~~text
id
connector_name
provider_event_id
event_type
correlation_key
payload_json
status                    RECEIVED | PROCESSED | FAILED
attempts
received_at
processed_at
last_error
~~~

Constraint: `UNIQUE(connector_name, provider_event_id)`

## OUTBOX_EVENT

~~~text
id
event_id
event_type
payload_json              the full envelope, carrying schema_version
status                    PENDING | CLAIMED | DELIVERED | DEAD_LETTER
attempts
max_attempts
next_attempt_at
claimed_by
claimed_at
last_error
created_at
processed_at
~~~

Constraint: `UNIQUE(event_id)`

## WEBHOOK_SUBSCRIPTION

~~~text
id
name
target_url
event_types_json          empty list means every event type
secret                    write-only; no read interface returns it
active
created_at
~~~

Reads return `has_secret` in place of `secret`.

## OUTBOX_DELIVERY

~~~text
id
outbox_event_id
subscription_id
status                    DELIVERED | FAILED
attempts
last_error
delivered_at
~~~

Constraint: `UNIQUE(outbox_event_id, subscription_id)`. A retry skips
subscriptions already recorded `DELIVERED`.

## SCHEMA_METADATA

~~~text
key                       primary key
value
updated_at
~~~

Holds `schema_version`. Its presence is what tells initialization that the
legacy migration has already run and must not run again.

# Part V: Vocabularies

## Node types

~~~text
HUMAN_TASK  DECISION  AUTOMATED_TASK  FORK  JOIN
SUBWORKFLOW  WAIT_SIGNAL  TIMER  MILESTONE  END
~~~

## Workflow execution status

~~~text
RUNNING  SUSPENDED  COMPLETED  FAILED  CANCELLED
~~~

## Node execution category

Declared by an FSM state, or resolved by the engine when absent.

~~~text
NOT_READY  READY  ACTIVE  WAITING  COMPLETED  SKIPPED  FAILED  CANCELLED
~~~

`COMPLETED` and `SKIPPED` satisfy a successor. So does `FAILED` when the node
declares `on_failure: CONTINUE`.

## Join rules

~~~text
ALL             every applicable predecessor is satisfied
ANY             at least one applicable predecessor is satisfied
N_OF_M          at least configuration.required_count are satisfied
ALL_REQUIRED    every predecessor is satisfied, applicable or not
~~~

## Failure and breach policies

~~~text
on_failure              FAIL_WORKFLOW (default) | SUSPEND | CONTINUE
child_failure_policy    FAIL (default) | CONTINUE
on_breach               NOTIFY (default) | ESCALATE | FAIL
~~~

## Guard operators

~~~text
eq  ne  in  not_in  exists  truthy
gt  gte  lt  lte  contains  starts_with
composed with all, any, not
~~~

Fields are dotted paths. Every operator is total: a type mismatch is false, not
an error. An unknown operator is rejected at publication.

## Permissions

~~~text
workflow.override       force a join past its rule
workflow.repair         manual intervention on a node
workflow.migrate        move an instance to another definition version
<definition's own>      any transition declaring required_permission
~~~

Each also requires a reason, and each is recorded as an event.

## Event types

Emitted by the engine:

~~~text
WORKFLOW_STARTED           WORKFLOW_COMPLETED          WORKFLOW_FAILED
WORKFLOW_FACTS_UPDATED     WORKFLOW_VERSION_MIGRATED   WORKFLOW_CANCELLED_BY_PARENT
WORKFLOW_SUSPENDED_ON_FAILURE

STEP_ACTIVATED             STEP_SKIPPED                STEP_DUE_AT_SET
STEP_SLA_BREACHED          SYSTEM_STEP_COMPLETED       JOIN_OVERRIDDEN

SIGNAL_RECEIVED            SIGNAL_CONSUMED
AUTOMATION_QUEUED          AUTOMATION_SUCCEEDED
AUTOMATION_RETRY_SCHEDULED AUTOMATION_FAILED
TIMER_SCHEDULED            TIMER_FIRED

CHILD_WORKFLOW_STARTED     SUBWORKFLOWS_COMPLETED
SUBWORKFLOWS_SETTLED       SUBWORKFLOW_FAILED
~~~

Three families are built from the action:

~~~text
STEP_{ACTION}       every step command, e.g. STEP_COMPLETE, STEP_CLAIM
WORKFLOW_{ACTION}   every lifecycle command, e.g. WORKFLOW_SUSPEND
REPAIR_{ACTION}     every repair, e.g. REPAIR_SKIP_STEP
~~~

Every event writes an outbox row in the same transaction, and every envelope
carries `schema_version`.

# Part VI: Step configuration

Keys read from `step_definition.configuration_json`. All optional unless the
node type requires them.

| Key | Node types | Meaning |
|---|---|---|
| `candidates` | human, decision | list of `{type, value, organization_id}` |
| `due_in_seconds` | any activatable | service level; schedules a breach timer |
| `on_breach` | with `due_in_seconds` | `NOTIFY`, `ESCALATE`, `FAIL` |
| `escalate_to` | with `ESCALATE` | assignee to escalate to; required |
| `escalate_to_type` | with `ESCALATE` | defaults to `ROLE` |
| `calendar` | with a deadline | business calendar; see below |
| `on_failure` | any | `FAIL_WORKFLOW`, `SUSPEND`, `CONTINUE` |
| `handler` | `AUTOMATED_TASK` | handler name for the worker |
| `input` | `AUTOMATED_TASK` | opaque input passed to the job |
| `max_attempts` | `AUTOMATED_TASK` | defaults to 3 |
| `signal_type` | `WAIT_SIGNAL` | required |
| `correlation_key` | `WAIT_SIGNAL` | narrows which signal matches |
| `delay_seconds` | `TIMER` | required |
| `required_count` | `JOIN` with `N_OF_M` | how many predecessors suffice |
| `child_workflow_version_id` | `SUBWORKFLOW` | creates a child automatically |
| `child_failure_policy` | `SUBWORKFLOW` | `FAIL`, `CONTINUE` |
| `title`, `business_type`, `business_key`, `correlation_key`, `relationship_type`, `relationship_key`, `required`, `variables` | `SUBWORKFLOW` | passed to the created child |
| `payload` | `TIMER` | carried on the timer and into its event |

A business calendar:

~~~json
{"business_days": [0, 1, 2, 3, 4],
 "opens_at": "09:00", "closes_at": "17:00",
 "holidays": ["2026-12-25"]}
~~~

Monday is 0. Omit the calendar for plain elapsed time. Publication rejects a
calendar with no business days, with `opens_at` at or after `closes_at`, or with
a malformed clock or holiday.

# Part VII: API surface

Twenty-five endpoints. Identity comes from the trusted actor header on every
command; see `../integration.md`.

~~~text
GET  /api/health

Definitions
GET  /api/templates
GET  /api/templates/{version_id}
POST /api/templates/import

Workflows
GET  /api/workflows
POST /api/workflows
GET  /api/workflows/{workflow_id}
POST /api/workflows/{workflow_id}/children
POST /api/workflows/{workflow_id}/actions
POST /api/workflows/{workflow_id}/facts
POST /api/workflows/{workflow_id}/signals
POST /api/workflows/{workflow_id}/external-events
POST /api/workflows/{workflow_id}/migrate-version

Work
GET  /api/work
POST /api/steps/{step_id}/actions
POST /api/steps/{step_id}/repair

Automation and timers
GET  /api/automation/jobs
POST /api/automation/jobs/{job_id}/result
POST /api/timers/process

Operations
GET  /api/operations/counters
GET  /api/operations/stuck
GET  /api/operations/dead-letters
POST /api/operations/dead-letters/{outbox_id}/redrive

Delivery
GET  /api/webhook-subscriptions
POST /api/webhook-subscriptions
~~~

Error mapping:

~~~text
ValidationError  -> 400        NotFoundError  -> 404
ConflictError    -> 409        ExecutionError -> 500
~~~

# Part VIII: Indexes

Thirteen, created after any migration because some reference columns the
migration adds.

~~~text
idx_workflow_business        workflow_instance(business_type, business_key)
idx_workflow_correlation     workflow_instance(correlation_id)
idx_workflow_parent          workflow_instance(parent_workflow_instance_id)
idx_workflow_execution       workflow_instance(execution_status, id)
idx_step_instance_workflow   step_instance(workflow_instance_id)
idx_step_execution           step_instance(execution_status, workflow_instance_id)
idx_assignment_open          work_assignment(assignee, status)
idx_signal_match             signal_receipt(workflow_instance_id, signal_type,
                                            correlation_key, consumed_at)
idx_automation_claim         automation_job(status, available_at, id)
idx_timer_due                durable_timer(status, due_at, id)
idx_event_workflow           workflow_event(workflow_instance_id, sequence_number)
idx_command_workflow         workflow_command(workflow_instance_id)
idx_outbox_status            outbox_event(status, next_attempt_at, id)
~~~

# Part IX: Conformance

The engine is conformant when each of these holds. Every row names the test that
proves it, so a claim here can be checked rather than believed.

| # | Criterion | Proven by |
|---|---|---|
| 1 | Domain-neutral templates execute unchanged | `test_engine` |
| 2 | Custom workflow and step FSMs control allowed transitions | `test_releases_1_3` |
| 3 | Invalid or cyclic graphs cannot be published | `test_validation` |
| 4 | Unknown guard operators are rejected at publication | `test_execution_semantics` |
| 5 | A state's declared execution category is honoured | `test_execution_semantics` |
| 6 | Required child workflows block their parent | `test_releases_1_3` |
| 7 | A child failure policy other than fail-the-parent is honoured | `test_execution_semantics` |
| 8 | Branches that cannot run are skipped, not left pending | `test_execution_semantics` |
| 9 | A failed node applies its declared policy | `test_execution_semantics` |
| 10 | Signals are durable, early and late safe, consumed once | `test_releases_1_3`, `contract` |
| 11 | Fact changes are versioned and auditable | `contract` |
| 12 | Candidate organization is enforced on claim | `test_releases_1_3`, `contract` |
| 13 | Automation jobs survive retry and lease loss | `contract`, `test_operations` |
| 14 | One job is claimed by exactly one worker | `contract`, `test_p0_defects` |
| 15 | Timers survive restart and respect a business calendar | `test_deadlines` |
| 16 | A service-level breach raises its configured action | `test_deadlines` |
| 17 | Duplicate commands and provider events have one effect | `test_engine`, `test_releases_1_3` |
| 18 | A retry returns current state, never a conflict | `test_p0_defects`, `test_operations` |
| 19 | Runtime changes emit ordered events and outbox rows | `test_engine`, `contract` |
| 20 | Delivery retries skip subscriptions already satisfied | `test_p0_defects` |
| 21 | Exceptional operations require permission and a reason | `test_operations`, `test_api` |
| 22 | No interface accepts self-asserted permissions | `test_api`, `test_embedding_isolation` |
| 23 | Secrets are never returned by a read interface | `test_p0_defects`, `test_api` |
| 24 | A suspended workflow performs no work | `test_p0_defects`, `contract` |
| 25 | Terminating cancels open work and cascades to children | `test_p0_defects` |
| 26 | A restart alters no workflow or node state | `test_p0_defects` |
| 27 | A host transaction commits a domain write and a transition together | `test_embedding` |
| 28 | Flow never commits, rolls back or closes a host connection | `test_embedding` |
| 29 | Flow's tables can be namespaced alongside a host's | `test_embedding_isolation` |
| 30 | Version migration is explicit, narrow and refused mid-flight | `test_version_migration` |
| 31 | Every adapter passes one contract suite | `contract`, `test_sqlite_contract` |

Criterion 31 is satisfied for SQLite only. Oracle is the intended production
adapter and does not exist yet; certifying it means subclassing
`RepositoryContract`, not writing a second suite. That gap is `NFR-3` in the
requirements and is the one requirement that cannot be closed without
infrastructure.
