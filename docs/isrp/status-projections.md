# RDBMS Status Projections

> Canonical model: All entity definitions, fields, relationships, constraints, and ownership decisions are consolidated in [Data model](data-model.md). This document describes behavior and uses abbreviated entity views only.

## Purpose

This document defines how request-level and assessment-level statuses are stored and maintained using the same PostgreSQL or Oracle database as the authoritative ISRP model.

The design intentionally separates explicit lifecycle state from derived operational summaries.


## Phase boundary

This status-projection design is a current-phase deterministic capability. Projection calculations use explicit relational state and versioned business rules. They do not call models, infer outcomes from document text, or depend on future AI services.

Future AI proposal events may cause the same deterministic projector to recalculate counts after an authorized human accepts and applies a proposal. AI output by itself does not change lifecycle, compliance, attention, or progress projections.

## Core decision

Use the primary RDBMS for:

- authoritative requests, assessments, requirements, work packages, and decisions
- explicit lifecycle status
- append-only status-transition history
- audit records
- OUTBOX_EVENT
- PROCESSED_EVENT
- derived request and assessment status projections

No NoSQL database, search engine, warehouse, Kafka cluster, or separate projection database is required for the initial implementation.

## Authoritative state versus derived summary

Authoritative lifecycle state changes only through an explicit FSM command.

Request lifecycle:

~~~text
DRAFT
SUBMITTED
CATEGORIZING
ASSESSMENTS_IN_PROGRESS
READY_TO_CLOSE
CLOSED
ON_HOLD
CANCELLED
~~~

Assessment lifecycle:

~~~text
DRAFT
PLANNED
IN_PROGRESS
READY_TO_COMPLETE
COMPLETED
ON_HOLD
CANCELLED
~~~

Derived values summarize inner execution:

- primary and active phases
- attention status and reason
- progress counts
- requirement outcome counts
- stale evidence and outdated implementation counts
- work-package queues
- findings and issues

Example:

~~~text
Assessment lifecycle: IN_PROGRESS
Primary phase: DESIGN_REVIEW
Attention: ACTION_REQUIRED
Requirements decided: 23 of 35
Not met: 2
Stale evidence: 4
~~~

## Authoritative request and assessment tables

ISRP_REQUEST includes:

~~~text
request_id
request_number
lifecycle_status
revision
workflow_instance_id
created_at
updated_at
submitted_at
closed_at
~~~

ISRP_ASSESSMENT includes:

~~~text
assessment_id
assessment_number
request_id
assessment_type
lifecycle_status
revision
workflow_instance_id
started_at
target_at
completed_at
created_at
updated_at
~~~

Lifecycle fields are not recalculated by dashboard queries. They change through validated commands such as SUBMIT_REQUEST, PLACE_ON_HOLD, MARK_READY_TO_CLOSE, CLOSE_REQUEST, START_ASSESSMENT, and COMPLETE_ASSESSMENT.

## Transition history

REQUEST_STATUS_HISTORY:

~~~text
status_history_id
request_id
previous_status
new_status
transition_name
reason
changed_by
changed_at
workflow_instance_id
step_instance_id
request_revision
~~~

ASSESSMENT_STATUS_HISTORY has the equivalent assessment fields.

These tables record explicit lifecycle transitions. They do not need a row for every derived count change because requirement, work-package, decision, evidence, finding, and issue records already provide authoritative history.

## Request status projection

REQUEST_STATUS_PROJECTION:

~~~text
request_id
lifecycle_status
primary_phase
attention_status
attention_reason

required_assessment_count
active_assessment_count
completed_assessment_count
blocked_assessment_count

met_requirement_count
partial_requirement_count
not_met_requirement_count
not_assessed_requirement_count
stale_evidence_count
outdated_implementation_count

open_finding_count
open_issue_count

source_version
projected_at
~~~

The lifecycle value is copied into the projection for convenient reads but remains authoritative on ISRP_REQUEST.

## Assessment status projection

ASSESSMENT_STATUS_PROJECTION:

~~~text
assessment_id
lifecycle_status
primary_phase
attention_status
attention_reason

total_requirement_count
submitted_requirement_count
decided_requirement_count
met_requirement_count
partial_requirement_count
not_met_requirement_count
not_applicable_requirement_count

stale_evidence_count
outdated_implementation_count
open_finding_count
open_issue_count

active_work_package_count
blocked_work_package_count
waiting_for_requestor_count
waiting_for_reviewer_count

source_version
projected_at
~~~

The lifecycle value remains authoritative on ISRP_ASSESSMENT.

## Parallel phases

An assessment can have several active DAG branches:

~~~text
Identity SME review: WAITING_FOR_REQUESTOR
Network SME review: IN_PROGRESS
Privacy SME review: COMPLETED
~~~

Do not force these into a misleading single phase. Maintain a primary phase for list screens and retain the full active set.

Preferred normalized model:

~~~text
ASSESSMENT_ACTIVE_PHASE
-----------------------
assessment_id
phase_code
phase_status
step_instance_id
started_at
updated_at
source_version
~~~

A PostgreSQL JSONB or Oracle JSON phase summary is acceptable for a compact read model, but the normalized table is easier to index and report.

## Outbox-driven update flow

A child business transaction writes authoritative records, audit, and an outbox event atomically:

~~~text
BEGIN

1. Insert or update authoritative business record
2. Append immutable domain history
3. Insert audit event
4. Insert OUTBOX_EVENT

COMMIT
~~~

After commit:

~~~text
OUTBOX_EVENT
  -> status projector
       -> recalculate assessment projection
       -> recalculate parent request projection
       -> record PROCESSED_EVENT
~~~

Example event types:

~~~text
ASSESSMENT_CREATED
ASSESSMENT_STATUS_CHANGED
WORK_PACKAGE_ASSIGNED
WORK_PACKAGE_STATUS_CHANGED
RESPONSE_SUBMITTED
REVIEWER_DETERMINATION_RECORDED
FINAL_DECISION_RECORDED
EVIDENCE_FRESHNESS_CHANGED
IMPLEMENTATION_CURRENCY_CHANGED
FINDING_STATUS_CHANGED
ISSUE_STATUS_CHANGED
~~~

## Projector transaction

The status projector processes an event using one RDBMS transaction:

~~~text
BEGIN

1. Check PROCESSED_EVENT for consumer and event ID
2. Verify aggregate version ordering
3. Recalculate or incrementally update assessment projection
4. Recalculate request projection
5. Insert PROCESSED_EVENT

COMMIT
~~~

PROCESSED_EVENT:

~~~text
consumer_name
event_id
processed_at

PRIMARY KEY (consumer_name, event_id)
~~~

Duplicate delivery is ignored. An older aggregate version is ignored or logged. A version gap is delayed, replayed, or repaired through targeted projection rebuild.

## Source versions

Every mutable aggregate has a monotonically increasing revision. Every outbox event carries the revision as aggregate_version. Every projection stores the latest applied source_version.

This provides:

- duplicate detection
- stale-event detection
- gap detection
- deterministic repair
- observable projection lag

For projections combining many children, retain the triggering source version and optionally a projection sequence generated by the projector.

## Request lifecycle roll-up

Evaluate configured rules in priority order:

~~~text
If explicitly CANCELLED:
    lifecycle = CANCELLED

Else if explicitly ON_HOLD:
    lifecycle = ON_HOLD

Else if no required assessments exist:
    lifecycle = CATEGORIZING

Else if all required assessments satisfy completion policy:
    lifecycle = READY_TO_CLOSE

Else:
    lifecycle = ASSESSMENTS_IN_PROGRESS
~~~

CLOSED always requires an explicit authorized closure command. Completion of child assessments alone does not automatically close the request.

Request attention priority:

~~~text
Any required assessment blocked
    -> BLOCKED

Any child requires remediation
    -> REMEDIATION_REQUIRED

Any child waits for requestor
    -> ACTION_REQUIRED

Any child waits for reviewer
    -> REVIEW_REQUIRED

Any child has stale/expired evidence
    -> EVIDENCE_REFRESH_REQUIRED

Otherwise
    -> NONE
~~~

Priority and combination behavior must be configurable. The projection can expose a primary attention status and a list of all active attention reasons.

## Assessment lifecycle roll-up

~~~text
If explicitly CANCELLED:
    lifecycle = CANCELLED

Else if explicitly ON_HOLD:
    lifecycle = ON_HOLD

Else if work has not started:
    lifecycle = PLANNED

Else if all required requirements have final decisions
        and all required work packages satisfy completion policy:
    lifecycle = READY_TO_COMPLETE

Else:
    lifecycle = IN_PROGRESS
~~~

COMPLETED requires an explicit authorized completion transition.

Assessment attention priority:

~~~text
Required work package blocked
    -> BLOCKED

Final decision NOT_MET
    -> REMEDIATION_REQUIRED

Clarification waits for responder
    -> ACTION_REQUIRED

Submitted response waits for reviewer
    -> REVIEW_REQUIRED

Evidence STALE or EXPIRED
    -> EVIDENCE_REFRESH_REQUIRED

Implementation OUTDATED
    -> IMPLEMENTATION_REVIEW_REQUIRED

Otherwise
    -> NONE
~~~

## Consistency expectations

The projection worker runs after the business transaction commits, so list and dashboard screens are eventually consistent. The normal delay should be measured and exposed operationally.

A command response may return:

~~~json
{
  "assessmentId": "ASMT-1002",
  "authoritativeRevision": 42,
  "projectionVersion": 41,
  "projectionPending": true
}
~~~

The UI can update optimistically or briefly display that the summary is refreshing.

## Closure validation

A projection is a query optimization, not an authorization source.

Close-assessment validation reads authoritative data:

1. Verify all required assessment requirements.
2. Verify final-decision authority and current decisions.
3. Verify required work packages and joins.
4. Evaluate findings, issues, exceptions, and closure policy.
5. Check the assessment revision.
6. Execute the assessment FSM transition.
7. Append status history, audit, and outbox records.

Close-request validation similarly checks every required assessment and request-level policy.

This prevents projection lag from incorrectly permitting closure.

## Rebuild and repair

Administrative operations include:

~~~text
Rebuild one assessment projection
Rebuild one request projection
Rebuild assessments for a request
Rebuild all active projections
Replay an event range
Inspect version gaps
Inspect projection lag
~~~

A rebuild reads authoritative current and history tables and overwrites only derived projection rows. It never changes domain records.

Incremental processing and a complete rebuild must produce the same projection for the same authoritative database state.

## PostgreSQL and Oracle mapping

The logical model is identical.

PostgreSQL:

- JSONB may hold compact phase or attention summaries.
- A worker can claim outbox rows with PostgreSQL locking semantics.
- Projection tables use normal relational indexes.

Oracle:

- An Oracle JSON-capable column may hold compact summaries.
- A worker can claim outbox rows with Oracle locking semantics.
- Oracle TxEventQ may later replace or augment the table relay without changing the projection contract.

The portable first implementation uses an OUTBOX_EVENT table and the same projector service with database-specific repository adapters.

## Initial deployment

~~~text
PostgreSQL or Oracle
  -> authoritative ISRP schemas
  -> audit/history schemas
  -> OUTBOX_EVENT
  -> request/assessment projection tables
  -> PROCESSED_EVENT

One status-projector process
  -> polls outbox
  -> updates projections
  -> records processed events
  -> reports lag, retries, and errors
~~~

External search, NoSQL, reporting, or vector stores can be added later as additional consumers of the same event contract.
