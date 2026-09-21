# ISRP Architecture Plan

## Purpose

ISRP handles intake, categorization, assessment execution, requirement review, validation, findings, issue integration, and review closure. Flow remains a generic workflow execution service and contains no information-security-specific rules or records.

## Service boundaries

### ISRP service

Owns:

- Request intake and request metadata
- Applications, technologies, vendors, products, and other subject references
- Duplicate and overlap detection
- Categorization and assessment creation
- Assessment scope and metadata
- Requirement catalogs and applicability
- Draft and submitted responses
- Evidence references
- Reviewer decisions, findings, and issue links
- Parent-child roll-up policy

### Workflow service

Owns:

- Immutable, versioned workflow definitions
- DAG nodes, edges, conditions, forks, and joins
- Workflow and step instances
- Step FSM definitions and transitions
- Human and automated work items
- Assignments, timers, retries, and escalation signals
- Idempotent commands, audit events, outbox events, and webhooks

The integration contract uses opaque references such as:

~~~text
business_type = ISRP_REQUEST
business_key  = ISR-10042
correlation_id = ISR-10042
~~~

The workflow service does not enforce ISRP requirement or assessment semantics.

## Runtime hierarchy

~~~text
ISRP Request
  Request lifecycle FSM
  Assessment orchestration DAG
    External assessment
      Assessment lifecycle FSM
      Assessment execution DAG
        Step FSMs
    Infrastructure assessment
      Assessment lifecycle FSM
      Assessment execution DAG
        Step FSMs
~~~

### Request

The request is the business container for a review. It can cover an application, a technology, a vendor or SaaS product, or a combination of subjects.

Recommended lifecycle:

~~~text
DRAFT
SUBMITTED
CATEGORIZING
ASSESSMENTS_IN_PROGRESS
READY_TO_CLOSE
CLOSED
~~~

Exceptional states:

~~~text
ON_HOLD
CANCELLED
~~~

The request DAG handles conditional creation, parallel execution, and joining of one or more assessments.

### Assessment

Each assessment is an independently owned unit of review, such as EXTERNAL, INTERNAL_APPLICATION, or INFRASTRUCTURE.

The assessment FSM expresses its coarse lifecycle. Its DAG expresses review phases and execution dependencies.

Example:

~~~text
Scope
  -> Design review
       -> SME identity review -----+
       -> SME network review ------+-> Consolidation
       -> SME privacy review ------+
  -> Build or implementation
  -> Validation
  -> Assessment closure
~~~

Workflow definitions are versioned. An active assessment continues on its selected version unless an explicit migration is performed.

### Step

Every executable DAG node has a step FSM. A human-review step may use:

~~~text
NOT_STARTED
ASSIGNED
IN_PROGRESS
WAITING_FOR_REQUESTOR
RESPONSE_RECEIVED
SUBMITTED
COMPLETED
~~~

Exceptional states include BLOCKED, FAILED, SKIPPED, and CANCELLED. Clarification can repeat within the step FSM without rebuilding the assessment DAG.

## Actor modes

Execution mode is independent of workflow state:

| Mode | Execution | Completion authority |
|---|---|---|
| HUMAN | A person performs the work | Authorized person |
| AI_ASSISTED_HUMAN | AI prepares a recommendation or draft | Authorized person |
| AI_AUTOMATED_SUPERVISED | AI performs an action | Human supervisor |
| AUTOMATION | A service performs a deterministic action | System policy |

A work item records its execution mode, assignee or candidate group, owning organization, supervisor where required, inputs, outputs, and completion authority.

AI activity must retain model reference, prompt/template version, input and output snapshots, policy result, confidence where meaningful, and human approval. AI output must not silently replace an accountable human decision.

## Parent-child status aggregation

The parent reflects inner execution through a projection, not by copying every child state.

Each request and assessment exposes:

- lifecycle_status: stable business lifecycle
- current_phase: best current phase or phase summary
- attention_status: action or blocker indicator
- progress: completed and required counts
- attention_reason: concise derived explanation

Example:

~~~text
Request lifecycle: ASSESSMENTS_IN_PROGRESS
Attention: ACTION_REQUIRED
Progress: 2 of 3 required assessments complete
Reason: External assessment is waiting for the requestor
~~~

Suggested request roll-up rules, evaluated in priority order:

1. Explicit request cancellation produces CANCELLED.
2. No assessments after submission produces CATEGORIZING.
3. All required assessments are terminal-successful produces READY_TO_CLOSE.
4. At least one required assessment is active produces ASSESSMENTS_IN_PROGRESS.
5. CLOSED requires an explicit request closure action after closure policy passes.

Suggested attention rules:

1. Any required child blocked produces BLOCKED.
2. Any child waiting for the requestor produces ACTION_REQUIRED.
3. Any child waiting for a reviewer produces REVIEW_REQUIRED.
4. Otherwise attention is NONE.

Child records remain authoritative. Parent projections can be rebuilt from children and events. A clarification loop changes attention and step status but normally leaves the assessment lifecycle IN_PROGRESS.


## Requirement execution boundary

Flow orchestrates when requirement work starts, who receives it, when clarification or approval is required, and when downstream DAG nodes may activate. ISRP owns the requirement catalog, selected assessment requirements, work packages, responses, evidence, comments, determinations, and final decisions.

A Flow step references an ISRP work package by opaque business keys:

~~~text
workflow step instance
    -> ISRP work package
        -> selected assessment requirements
            -> responder assertions
            -> evidence versions and citations
            -> reviewer determinations
            -> final decisions
~~~

A work package is a distribution unit, not the compliance system of record. The same assessment requirement may appear in a responder package, an SME review package, and an approval package with different authorities.

Requirement state is independent of the assessment DAG:

~~~text
NOT_STARTED
DRAFT
SUBMITTED
IN_REVIEW
CLARIFICATION_REQUIRED
RESUBMITTED
DECIDED
~~~

Flow receives business events such as PACKAGE_SUBMITTED, CLARIFICATION_REQUESTED, PACKAGE_COMPLETED, and REQUIREMENT_DECIDED. It evaluates configured join and transition conditions without interpreting the security meaning of MET or NOT_MET.

Compliance is represented by separate dimensions:

- outcome: NOT_ASSESSED, MET, PARTIALLY_MET, NOT_MET, or NOT_APPLICABLE
- implementation currency: UNKNOWN, CURRENT, OUTDATED, PLANNED_REPLACEMENT, or DECOMMISSIONED
- evidence freshness: NOT_PROVIDED, CURRENT, STALE, EXPIRED, or NOT_REQUIRED

The authoritative chain is responder assertion, reviewer determination, and final decision. One actor never overwrites another actor's conclusion. See [Flow to IS Requirements](requirements-catalog.md) for the complete model.


## RDBMS-first status projections

The initial implementation keeps authoritative records, outbox events, and request/assessment status projections in the same PostgreSQL or Oracle database. A separate NoSQL or search store is not required.

Lifecycle status is authoritative and changes only through an authorized FSM transition. Operational summaries are derived:

- lifecycle_status: explicit request or assessment state
- primary_phase and active phases: DAG execution summary
- attention_status and reason: derived action or blocker
- progress counts: derived child completion
- compliance, evidence, finding, and issue counts: derived ISRP summary

~~~text
Child business change
  -> authoritative history and current pointer
  -> audit event
  -> OUTBOX_EVENT
  -> commit
  -> status projector
       -> ASSESSMENT_STATUS_PROJECTION
       -> REQUEST_STATUS_PROJECTION
~~~

The projector is asynchronous and idempotent. Every consumer records processed event IDs and source versions. Projection lag is acceptable for dashboards, but lifecycle commands such as close, cancel, or reopen validate authoritative records rather than trusting a potentially delayed projection.

A request becomes READY_TO_CLOSE only when required assessment conditions pass. CLOSED requires a separate authorized closure transition. An assessment similarly becomes READY_TO_COMPLETE from child conditions, while COMPLETED requires an explicit transition.

Parallel DAG branches are represented by a primary phase plus normalized active-phase rows or a JSON phase summary. A single current phase must not conceal simultaneously active reviews. See [RDBMS status projections](status-projections.md).

## Editing and submission semantics

Request and assessment metadata maintain a current editable representation. Updates use optimistic locking and overwrite current values, while audit events preserve who changed what and when.

Requirement responses use different semantics:

1. An active draft is editable and can be overwritten.
2. Submission creates an immutable response and responder assertion.
3. Reviewer feedback and determinations reference a specific submitted version.
4. A clarification cycle creates a new draft, normally initialized from the last submission.
5. Resubmission creates another immutable version.
6. Final decisions are created only by configured decision authorities and never overwrite responder or reviewer records.
7. Evidence replacement creates a new evidence version; prior files and citations remain historically addressable.
8. Notes and commentary are append-only, with corrections expressed as new linked comments.

## Reliability rules

- Commands carry client-generated idempotency keys.
- Transitions validate the current revision before committing.
- Business updates and outbox records commit in the same database transaction.
- Consumers handle events idempotently.
- Issue creation and external automation never run inside the primary database transaction.
- Long-running work uses durable timers and retry policies.
