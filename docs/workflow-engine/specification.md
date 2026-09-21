# Flow Workflow Engine Specification

## 1. Purpose

Flow is a configurable, domain-neutral workflow execution service. It coordinates long-running business processes without owning the business records being coordinated.

This specification covers:

- architecture and service boundaries
- definition-time and runtime models
- workflow lifecycle FSMs
- DAG orchestration
- node-level FSMs
- parent-child workflows
- assignments and work iterations
- controlled facts and conditional routing
- signals, automation jobs, and timers
- audit, inbox, outbox, idempotency, and recovery
- current HTTP integration surfaces
- persistence and deployment direction
- security and operational requirements

The specification reflects the Release 1–3 baseline. SQLite is the development adapter. Oracle is the next production adapter; PostgreSQL follows Oracle.

## 2. Design goals

Flow must:

1. Remain independent of any one business domain.
2. Express stable lifecycle state separately from execution mechanics.
3. Support deterministic, versioned FSM and DAG definitions.
4. Coordinate human, service, timer, signal, and child-workflow work.
5. Preserve execution history and idempotency across retries.
6. Allow client services to retain authority over business data and decisions.
7. Support long-running processes that survive restarts.
8. Be portable across SQLite, Oracle, and PostgreSQL adapters.
9. Prevent workflow definitions from executing arbitrary code.
10. Make exceptional operations explicit, authorized, and auditable.

Flow is not:

- a business-domain database
- a general rules engine
- a document or evidence repository
- an identity directory
- an arbitrary-code execution platform
- a replacement for domain-specific authorization and policy

## 3. Core architectural decision

Every workflow instance has three distinct execution layers:

~~~text
WORKFLOW INSTANCE
    |
    +-- Lifecycle FSM
    |     Stable, business-facing lifecycle state
    |
    +-- Execution DAG
          Nodes, branches, joins, signals, timers, and children
              |
              +-- Node FSM
                    Assignment, work, waiting, clarification,
                    submission, completion, or failure
~~~

The layers interact, but they are not the same state machine.

Examples:

~~~text
Lifecycle state:        FULFILLING
Workflow execution:     RUNNING
Active DAG nodes:       Pack order, arrange shipment
Pack-order node state:  IN_PROGRESS
Shipment node state:    QUEUED
~~~

## 4. System context

~~~text
                           +-------------------------+
                           | Identity / API Gateway  |
                           +------------+------------+
                                        |
                                        v
+------------------+       commands     +-------------------------+
| Business Service | -----------------> | Workflow API            |
|                  |                    |                         |
| Owns domain data | <----------------- | Queries and responses   |
+--------+---------+       events       +------------+------------+
         |                                           |
         |                                           v
         |                               +-------------------------+
         |                               | Workflow Core           |
         |                               | FSM + DAG orchestration |
         |                               +------------+------------+
         |                                            |
         |                                            v
         |                               +-------------------------+
         |                               | Persistence Adapter     |
         |                               | SQLite / Oracle / PG    |
         |                               +------------+------------+
         |                                            |
         |                      +---------------------+--------------------+
         |                      |                     |                    |
         v                      v                     v                    v
+------------------+  +------------------+  +------------------+  +------------------+
| Webhook Consumer |  | Automation      |  | Timer Worker     |  | Admin / Ops      |
| or Event Relay   |  | Workers         |  |                  |  |                  |
+------------------+  +------------------+  +------------------+  +------------------+
~~~

## 5. Service boundary

### 5.1 Flow owns

- workflow and FSM definitions
- immutable published versions
- lifecycle state transitions
- workflow and node execution state
- graph activation and join evaluation
- parent-child workflow relationships
- work candidates and execution assignments
- work iterations and attempts
- controlled orchestration facts
- signals and their consumption
- automation-job execution records
- durable timers
- workflow execution events
- command deduplication
- integration inbox
- transactional outbox and webhook delivery state

### 5.2 Client services own

- business aggregates and metadata
- business validation and policy
- authoritative decisions and outcomes
- documents, evidence, and attachments
- customer, product, application, or case records
- business comments and collaboration history
- business-level reporting
- business closure eligibility

### 5.3 Integration rule

Flow stores opaque business references:

~~~text
business_type
business_key
correlation_id
subject_type
subject_id
source_system
result_reference
relationship_type
relationship_key
~~~

Flow must not dereference or interpret these values inside workflow-core.

# Part I: Architecture

## 6. Logical components

### 6.1 Workflow API

Responsibilities:

- validate request shape
- pass authenticated actor context to workflow-core
- map domain errors to HTTP responses
- expose definition, runtime, signal, work, job, timer, and webhook endpoints
- avoid implementing orchestration rules

### 6.2 Workflow core

Responsibilities:

- validate templates and FSMs
- enforce FSM transitions
- enforce optimistic revisions
- evaluate constrained guards
- activate DAG nodes
- evaluate joins
- coordinate child workflows
- consume signals
- create automation jobs and timers
- emit ordered execution events

Workflow core depends only on repository ports.

### 6.3 Persistence adapters

Responsibilities:

- implement atomic transactions
- persist definitions and runtime state
- enforce uniqueness and foreign keys
- implement command deduplication
- claim jobs, timers, inbox, and outbox work safely
- map JSON, timestamp, locking, and identifier differences

Adapter order:

~~~text
Development:       SQLite
Production first:  Oracle
Production second: PostgreSQL
~~~

### 6.4 Delivery worker

Responsibilities:

- claim outbox records
- select matching subscriptions
- sign webhook messages
- deliver with retry and backoff
- preserve event identity
- record per-subscription delivery results
- place exhausted records in dead-letter status

### 6.5 Automation workers

Responsibilities:

- claim automation jobs using a lease
- execute configured deterministic handlers outside workflow-core
- report success, retryable failure, or terminal failure
- return opaque result references

### 6.6 Timer worker

Responsibilities:

- find due durable timers
- claim or fire each timer idempotently
- execute the configured timer action
- emit workflow events

## 7. Deployment view

Initial development deployment:

~~~text
+---------------------------------------------------+
| Docker Compose                                    |
|                                                   |
|  workflow-api -----+                              |
|                    +---- SQLite volume            |
|  workflow-worker --+                              |
|                                                   |
|  React console served by workflow-api             |
+---------------------------------------------------+
~~~

Production target:

~~~text
+-------------------+       +-----------------------+
| API instances     | ----> | Oracle                |
| stateless         |       | authoritative runtime |
+-------------------+       +-----------+-----------+
                                            ^
+-------------------+                       |
| Automation workers| ----------------------+
+-------------------+                       |
                                            |
+-------------------+                       |
| Timer workers     | ----------------------+
+-------------------+                       |
                                            |
+-------------------+                       |
| Delivery workers  | ----------------------+
+-------------------+
~~~

Workers and API instances may scale independently. Database-specific claiming must prevent two workers from owning the same unit of work simultaneously.

# Part II: High-level design

## 8. Definition model

Definitions are immutable after publication.

~~~text
WORKFLOW_DEFINITION
    |
    +--< WORKFLOW_VERSION
           |
           +--> lifecycle FSM version
           |
           +--< STEP_DEFINITION
           |      |
           |      +--> step FSM version
           |
           +--< TRANSITION_DEFINITION

FSM_DEFINITION
    |
    +--< FSM_VERSION
           |
           +--< FSM_STATE_DEFINITION
           +--< FSM_TRANSITION_DEFINITION
~~~

A running workflow remains pinned to its selected workflow and FSM versions.

## 9. Runtime model

~~~text
WORKFLOW_INSTANCE
    |
    +--< WORKFLOW_SUBJECT
    +--< WORKFLOW_FACT_HISTORY
    +--< STEP_INSTANCE
    |      |
    |      +--< STEP_ATTEMPT
    |      +--< WORK_CANDIDATE
    |      +--< WORK_ASSIGNMENT
    |      +--< AUTOMATION_JOB
    |      +--< DURABLE_TIMER
    |
    +--< SIGNAL_RECEIPT
    +--< WORKFLOW_EVENT
    +--< WORKFLOW_COMMAND
    +--< OUTBOX_EVENT
    |
    +--< child WORKFLOW_INSTANCE
~~~

## 10. Workflow creation

~~~text
Start command
    |
    v
Check command_id
    |
    +-- already completed --> return original result
    |
    v
Load published workflow version
    |
    v
Create workflow instance
    |
    +-- set lifecycle initial state
    +-- set execution status RUNNING
    +-- store business references and subjects
    +-- create one instance per defined node
    |
    v
Append WORKFLOW_STARTED and OUTBOX_EVENT
    |
    v
Drive DAG until stable
    |
    v
Commit command result atomically
~~~

## 11. DAG driving

The driver repeatedly:

1. Loads the current workflow.
2. Stops if execution is not RUNNING.
3. Finds NOT_READY nodes whose applicable predecessors are satisfied.
4. Activates those nodes.
5. Performs immediate system-node behavior.
6. Re-evaluates waiting subworkflows and signals.
7. Repeats until no state changes occur.

The driver has a bounded iteration count to detect unstable execution behavior.

## 12. FSM transition handling

For a workflow or step command:

~~~text
Current state + action
        |
        v
Find transition in pinned FSM version
        |
        +-- none --> conflict
        |
        v
Evaluate guard
        |
        +-- false --> conflict
        |
        v
Check permission and mandatory reason
        |
        +-- denied --> conflict
        |
        v
Apply target state and execution effects
        |
        v
Append event + outbox
        |
        v
Drive DAG and commit
~~~

## 13. Parent-child workflows

A SUBWORKFLOW node may:

- create a configured child automatically
- wait for children explicitly created by a client
- own one or more child workflow instances
- distinguish required and optional children
- fail or continue according to child-failure policy

~~~text
Parent workflow
    |
    +-- SUBWORKFLOW node
           |
           +-- required child A
           +-- required child B
           +-- optional child C
~~~

The parent node completes when its awaited children satisfy policy. Child business types and relationship values remain opaque.

## 14. Signals

Signals represent durable external facts or events that affect orchestration.

Properties:

- unique command identity
- workflow scope
- signal type
- optional correlation key
- opaque payload
- receipt timestamp
- optional consumption timestamp and node

Signals may arrive:

- before the matching WAIT_SIGNAL node activates
- while the node is waiting
- after a duplicate delivery

The first matching unconsumed signal is consumed exactly once. Duplicate commands return the prior result.

## 15. Controlled facts

Workflow facts are orchestration inputs, not a substitute for the domain model.

Every changed fact records:

- workflow
- fact key
- previous value
- new value
- source type
- source reference
- actor
- workflow revision
- timestamp

Guards evaluate a constrained JSON language:

~~~text
eq
ne
in
not_in
exists
truthy
all
any
not
~~~

No arbitrary Python, SQL, or network call is allowed.

## 16. Human work and assignments

Each human or decision node may define:

- candidate users
- candidate roles
- candidate groups
- candidate organizations
- default assignment role
- owning organization in configuration

Runtime assignments retain:

- assignee type and value
- organization
- assigning actor
- assignment reason
- mandatory flag
- status
- due date
- start and end timestamps

Candidate checks are enforced when an actor claims work using structured actor context.

## 17. Work attempts and clarification

One node instance can have successive attempts.

~~~text
Attempt 1
  start -> clarification -> response -> complete/rework

Attempt 2
  reopen -> start -> complete
~~~

The client domain stores business drafts, comments, attachments, and decisions. Flow stores the attempt and opaque result reference.

## 18. Automation jobs

AUTOMATED_TASK nodes create durable jobs instead of executing application logic inside the API transaction.

~~~text
READY
  |
  v
QUEUED
  |
  v
RUNNING
  |
  +--> SUCCEEDED ------> node completed
  |
  +--> RETRY_WAIT -----> available for another claim
  |
  +--> FAILED ---------> node failed
~~~

Each job records:

- stable job key
- workflow and node
- handler name
- attempt count and maximum attempts
- input and result JSON
- availability time
- claimant and lease
- completion time
- last error

## 19. Timers

TIMER nodes create durable timer records.

Current timer-node behavior:

~~~text
Node activates
    |
    v
Create timer with due_at
    |
    v
Node waits
    |
    v
Worker fires timer
    |
    v
Node completes and DAG continues
~~~

Timer identity prevents duplicate creation for the same node iteration.

## 20. Reliable events

Every execution event and its outbound message are written in the same transaction.

~~~text
Business command
    |
    v
Runtime update
    |
    +-- WORKFLOW_EVENT
    +-- OUTBOX_EVENT
    |
    v
Commit
    |
    v
Delivery worker
    |
    +-- delivered
    +-- retry with backoff
    +-- dead letter
~~~

External provider events enter through INBOX_EVENT and are translated into idempotent signals.

# Part III: Low-level design

## 21. FSM entities

### FSM_DEFINITION

~~~text
id
key                  globally unique stable name
name
scope                WORKFLOW | STEP
created_at
~~~

### FSM_VERSION

~~~text
id
definition_id
version_number
status                DRAFT | PUBLISHED | RETIRED
initial_state
created_at
published_at
~~~

### FSM_STATE_DEFINITION

~~~text
id
fsm_version_id
state_key
terminal
~~~

### FSM_TRANSITION_DEFINITION

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

Constraint:

~~~text
UNIQUE(fsm_version_id, from_state, action)
~~~

## 22. Workflow definition entities

### WORKFLOW_DEFINITION

~~~text
id
key
name
description
domain
status
created_at
~~~

### WORKFLOW_VERSION

~~~text
id
definition_id
version_number
status
lifecycle_fsm_version_id
created_at
published_at
~~~

### STEP_DEFINITION

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

Supported step types:

~~~text
HUMAN_TASK
DECISION
AUTOMATED_TASK
FORK
JOIN
SUBWORKFLOW
WAIT_SIGNAL
TIMER
MILESTONE
END
~~~

### TRANSITION_DEFINITION

~~~text
id
workflow_version_id
from_step_id
to_step_id
condition_json
priority
~~~

## 23. Workflow runtime entities

### WORKFLOW_INSTANCE

~~~text
id
workflow_version_id
title
business_type
business_key
correlation_id
status                       compatibility projection
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

created_by
created_at
completed_at
suspended_at
cancelled_at
~~~

Execution status:

~~~text
RUNNING
SUSPENDED
COMPLETED
FAILED
CANCELLED
~~~

### WORKFLOW_SUBJECT

~~~text
id
workflow_instance_id
subject_type
subject_id
source_system
relationship
~~~

### WORKFLOW_FACT_HISTORY

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

## 24. Node runtime entities

### STEP_INSTANCE

~~~text
id
workflow_instance_id
step_definition_id
iteration_number
state
execution_status
result_json
activated_at
started_at
completed_at
~~~

Node execution status:

~~~text
NOT_READY
READY
ACTIVE
WAITING
COMPLETED
SKIPPED
FAILED
CANCELLED
~~~

### STEP_ATTEMPT

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

### WORK_CANDIDATE

~~~text
id
step_instance_id
candidate_type
candidate_value
organization_id
~~~

### WORK_ASSIGNMENT

~~~text
id
step_instance_id
assignee_type
assignee
organization_id
assigned_by
reason
mandatory
status
due_at
created_at
ended_at
~~~

## 25. Signal, automation, and timer entities

### SIGNAL_RECEIPT

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

### AUTOMATION_JOB

~~~text
id
job_key
workflow_instance_id
step_instance_id
handler
status
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

### DURABLE_TIMER

~~~text
id
timer_key
workflow_instance_id
step_instance_id
timer_type
action
due_at
payload_json
status
fired_at
cancelled_at
~~~

## 26. Reliability entities

### WORKFLOW_COMMAND

~~~text
command_id             primary key
workflow_instance_id
action
result_json
processed_at
~~~

The same command ID returns the original result.

### WORKFLOW_EVENT

~~~text
id
event_id               globally unique
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

Constraint:

~~~text
UNIQUE(workflow_instance_id, sequence_number)
~~~

### INBOX_EVENT

~~~text
id
connector_name
provider_event_id
event_type
correlation_key
payload_json
status
attempts
received_at
processed_at
last_error
~~~

Constraint:

~~~text
UNIQUE(connector_name, provider_event_id)
~~~

### OUTBOX_EVENT

~~~text
id
event_id
event_type
payload_json
status
attempts
max_attempts
next_attempt_at
claimed_by
claimed_at
last_error
created_at
processed_at
~~~

### WEBHOOK_SUBSCRIPTION and OUTBOX_DELIVERY

Subscriptions select event types and provide a target URL and signing secret. Delivery records preserve independent status and attempts per subscription.

## 27. Join semantics

Applicable incoming edges are evaluated against the workflow fact snapshot.

~~~text
ALL
    Every applicable predecessor must be satisfied.

ANY
    At least one applicable predecessor must be satisfied.

N_OF_M
    At least configuration.required_count applicable predecessors
    must be satisfied.

ALL_REQUIRED
    All applicable predecessors are required. Optionality is
    normally expressed through edge conditions.
~~~

Satisfied predecessor execution states:

~~~text
COMPLETED
SKIPPED
~~~

An authorized actor with `workflow.override` may override a JOIN with a mandatory reason. The override is recorded as an event.

## 28. Template validation

Publication validation requires:

- at least one node
- unique node keys
- supported node types
- exactly one root
- at least one END node
- no transition from END
- no duplicate edge
- all transition endpoints exist
- every non-END node has an outgoing edge
- every node is reachable from the root
- every node can reach an END
- graph is acyclic
- JOIN has a supported rule
- WAIT_SIGNAL declares signal_type
- TIMER declares delay_seconds
- embedded FSM states and transitions are valid

## 29. Concurrency

Client mutations use optimistic locking:

~~~text
expected_revision == current workflow revision
~~~

If the values differ, Flow returns a conflict and performs no mutation.

Worker work uses database claiming:

- automation job lease
- timer status transition
- outbox claim and stale-claim recovery
- unique job, timer, event, command, and provider-event keys

Oracle and PostgreSQL adapters must provide equivalent semantics using their native locking mechanisms.

## 30. API surface

Definition:

~~~text
GET  /api/templates
GET  /api/templates/{version_id}
POST /api/templates/import
~~~

Workflow:

~~~text
GET  /api/workflows
POST /api/workflows
GET  /api/workflows/{workflow_id}
POST /api/workflows/{workflow_id}/children
POST /api/workflows/{workflow_id}/actions
POST /api/workflows/{workflow_id}/facts
POST /api/workflows/{workflow_id}/signals
POST /api/workflows/{workflow_id}/external-events
~~~

Work:

~~~text
POST /api/steps/{step_id}/actions
~~~

Automation and timers:

~~~text
GET  /api/automation/jobs
POST /api/automation/jobs/{job_id}/result
POST /api/timers/process
~~~

Delivery:

~~~text
GET  /api/webhook-subscriptions
POST /api/webhook-subscriptions
~~~

## 31. Security model

The API accepts structured actor context:

~~~text
actor_id
actor_type
organization_id
roles
groups
permissions
~~~

The production gateway must authenticate and sign or inject this context. Clients must not be allowed to self-assert permissions.

Current engine checks include:

- transition-level required permission
- mandatory reason
- organization-aware candidate claim
- workflow revision
- join-override permission

Production requirements include:

- gateway authentication
- service-to-service identity
- authorization policy administration
- secret encryption
- separation-of-duty extensions
- audit export
- rate limiting
- data classification and retention

## 32. Failure and recovery

### API failure before commit

The transaction rolls back. The same command may be retried.

### API response lost after commit

The client repeats command_id and receives the stored result.

### Automation worker failure

The lease expires and another worker may reclaim the job.

### Webhook delivery failure

The outbox record returns to PENDING with backoff. Exhausted attempts become DEAD_LETTER.

### Duplicate provider event

The connector/provider unique key prevents a second inbox record. The translated signal command is also idempotent.

### Projection or consumer failure

The event remains replayable by immutable event ID and workflow sequence.

## 33. Non-functional requirements

### Correctness

- atomic runtime mutation, event, and outbox write
- no duplicate command effect
- immutable published definitions
- deterministic guard evaluation
- ordered events per workflow

### Availability

- stateless API instances
- restart-safe timers and jobs
- retryable external delivery
- no in-memory-only orchestration state

### Performance

- indexed workflow business and correlation references
- indexed active jobs, timers, signals, and outbox records
- bounded driver loop
- paginated runtime and audit queries in production adapters

### Observability

Production adapters and workers should expose:

- command and transition latency
- active, waiting, suspended, failed workflow counts
- automation queue depth and lease expiry
- due-timer lag
- inbox and outbox lag
- retry and dead-letter counts
- webhook delivery latency

## 34. Versioning and compatibility

- Published definitions and FSM versions are immutable.
- New instances select a specific published version.
- Existing instances remain pinned.
- SQLite initialization performs additive migration from the 0.2 schema.
- Workflow-instance migration between definition versions requires an explicit mapping policy and is not implicit.
- API additions should remain backward compatible within a major version.

## 35. Current implementation boundary

Implemented in Release 1–3:

- capabilities described in this specification using SQLite
- compatibility with the original ISR example
- Python conformance tests for FSM, DAG, hierarchy, signal, fact, assignment, automation, timer, lifecycle, idempotency, and outbox behavior

Next:

1. Oracle production adapter and conformance profile
2. production authentication and secret management
3. broader SLA/escalation actions and administrative repair UI
4. PostgreSQL adapter after Oracle stabilizes

## 36. Acceptance criteria

The engine is considered conformant when:

1. Existing domain-neutral templates still execute.
2. Custom workflow and step FSMs control allowed transitions.
3. Invalid or cyclic DAGs cannot be published.
4. Required child workflows block their parent until complete.
5. Signals are durable, early/late safe, and idempotent.
6. Fact changes are versioned and auditable.
7. Candidate organization is enforced for claims using structured actors.
8. Automation jobs survive retries and worker lease loss.
9. Timers survive process restart.
10. Duplicate commands and provider events have one effect.
11. Runtime changes emit ordered events and transactional outbox records.
12. Exceptional operations require configured permission and reason.
