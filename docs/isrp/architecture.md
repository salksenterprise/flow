# ISRP Architecture Plan

## Purpose

ISRP handles intake, categorization, assessment execution, requirement review,
validation, findings, issue integration, and review closure. Flow remains a
generic workflow execution core with no information-security-specific rules or
records. ISRP embeds that core in-process; Flow is not deployed as a shared
workflow service.

## Embedded architecture and ownership boundaries

### ISRP host application

Owns:

- Request intake and request metadata
- Applications, technologies, vendors, products, and other subject references
- Duplicate and overlap detection
- Categorization and assessment creation
- Assessment scope and metadata
- Requirement catalogs and applicability
- Draft and submitted responses
- Evidence references
- Reviewer decisions, findings, remediation cases, CAPs, and external issue references
- Parent-child roll-up policy

### Embedded Flow core

Owns:

- Immutable, versioned workflow definitions
- DAG nodes, edges, conditions, forks, and joins
- Workflow and step instances
- Step FSM definitions and transitions
- Human and automated work items
- Assignments, timers, retries, and escalation signals
- Idempotent commands, execution audit events, and execution outbox events

The boundary is one of module hygiene, not of deployment: both run in one
process against one database. ISRP owns the connection and the transaction.
The orchestration joins that transaction and never commits, rolls back, or
closes it.

~~~text
ISRP command
  -> begin transaction
  -> update ISRP domain records
  -> call the orchestration
  -> it updates execution state and appends events and outbox rows
  -> commit or roll back both sets of changes together
~~~

There are no opaque business references to supply. A run belongs to an ISRP
request or an ISRP assessment, named by the pair:

~~~text
owner_type = ISRP_REQUEST      owner_type = ISRP_ASSESSMENT
owner_id   = 10042             owner_id   = 20017
~~~

Those are the rows themselves: orchestration state lives on `isrp_request` and
`isrp_assessment` beside the business columns, so there is one revision to lock
against and no pair of records that can disagree. The orchestration modules
still do not import ISRP's domain models; they take identifiers, statuses and
configuration.

### Deployment and database decision

~~~text
Local development and tests    SQLite adapter, currently executable
Intended production            Oracle adapter, not yet implemented or verified
Later optional adapter         PostgreSQL, only if a demonstrated need appears
~~~

Oracle conformance cannot be proven in the local environment. No document or
release may claim Oracle support until the Oracle adapter passes the same
repository contract suite against a real Oracle instance. The ISRP application
invokes Flow migrations through its own deployment process and schedules Flow's
timer, job, and outbox routines through host-managed jobs.

### Open dependencies on Flow

Three things this design assumes are not provided by Flow as it stands. Each has
a workaround; each is recorded so it is not discovered during implementation.

1. **`BLOCKED` is not a Flow execution category.** Flow has `NOT_READY`,
   `READY`, `ACTIVE`, `WAITING`, `COMPLETED`, `SKIPPED`, `FAILED` and
   `CANCELLED`. A blocked step FSM state maps to `WAITING`, and ISRP derives
   `BLOCKED` in its attention projection from its own records. The alternative
   is to ask for the category in Flow.
2. **Flow does not enforce execution modes.** Iteration 8 calls for publication
   validation that rejects AI modes. Flow has no execution-mode field and its
   validation cannot reject one. ISRP must validate its own templates before
   calling `import_template`, or the field must be added to Flow.
3. **`ON_HOLD` cannot return to the previous state automatically.** Flow's FSM
   transitions are static `from -> to` pairs, so "resume to whatever state you
   were in" is not expressible. The request and assessment lifecycle FSMs need
   one explicit resume transition per source state.

ISRP's own step vocabulary is otherwise fully supported: Flow lets each state
declare its execution category, so the table above maps onto the engine without
Flow recognising any of the names.

## Runtime hierarchy

For a diagram-led explanation of these layers, see the [Workflow visual guide](workflow-visual-guide.md).

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

Every executable DAG node has a step FSM. The human-review FSM is defined once,
here, and drawn in the [workflow visual guide](workflow-visual-guide.md); an
earlier revision of this document listed a different set of states from the
guide, which would have left the implementer to choose.

Each state declares the Flow execution category it maps to, which is what lets
ISRP use its own vocabulary without Flow having to recognise the names.

| Step state | Flow category | Meaning |
|---|---|---|
| `NOT_STARTED` | `NOT_READY` | predecessors not yet satisfied |
| `AVAILABLE` | `READY` | activatable, nobody has claimed it |
| `ASSIGNED` | `READY` | claimed or assigned, not started |
| `IN_PROGRESS` | `ACTIVE` | the responder is working |
| `SUBMITTED` | `ACTIVE` | handed to review |
| `IN_REVIEW` | `ACTIVE` | a reviewer or SME is deciding |
| `WAITING_FOR_RESPONSE` | `WAITING` | clarification requested, waiting on the requestor |
| `RESPONSE_RECEIVED` | `ACTIVE` | clarification answered, back with the reviewer |
| `BLOCKED` | `WAITING` | see the open dependency below |
| `COMPLETED` | `COMPLETED` | terminal |
| `SKIPPED` | `SKIPPED` | terminal |
| `FAILED` | `FAILED` | terminal |
| `CANCELLED` | `CANCELLED` | terminal |

Clarification repeats `IN_REVIEW -> WAITING_FOR_RESPONSE -> RESPONSE_RECEIVED ->
IN_REVIEW` without rebuilding the assessment DAG.

## Actor modes and phase boundary

### Current phase

Published ISRP workflows support only:

| Mode | Execution | Completion authority |
|---|---|---|
| HUMAN | A person performs intake, response, review, or approval work | Authorized person |
| AUTOMATION | A service performs deterministic rules, validation, routing, timers, projection, or integration work | Configured system policy |

A work item records its execution mode, assignee or candidate group, owning organization, inputs, outputs, and completion authority.

Current automation does not infer security meaning from unstructured evidence, determine whether a requirement is met, generate requirement responses, or propose citations. Requirement selection uses versioned business rules and structured intake values. Security conclusions and evidence citations are created or verified by authorized humans.

### Future phase

AI_ASSISTED_HUMAN and AI_AUTOMATED_SUPERVISED are reserved for separately published future workflow versions. Future AI creates proposals only; an authorized human approval and deterministic apply step produce authoritative ISRP changes.

Future AI activity must retain model reference, prompt/template version, exact evidence-version inputs, output snapshot, policy result, confidence where meaningful, and human disposition. See [Current and future phases](phases.md).

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

Attention rules, in priority order. [RDBMS status projections](status-projections.md)
is canonical for these; this list mirrors it rather than restating a shorter
version, which an earlier revision of this document did.

1. Any required child blocked produces BLOCKED.
2. Any child requiring remediation produces REMEDIATION_REQUIRED.
3. Any child waiting for the requestor produces ACTION_REQUIRED.
4. Any child waiting for a reviewer produces REVIEW_REQUIRED.
5. Any child with stale or expired evidence produces EVIDENCE_REFRESH_REQUIRED.
6. Otherwise attention is NONE.

Child records remain authoritative. Parent projections can be rebuilt from children and events. A clarification loop changes attention and step status but normally leaves the assessment lifecycle IN_PROGRESS.


## Requirement execution boundary

The embedded Flow core orchestrates when requirement work starts, who receives
it, when clarification or approval is required, and when downstream DAG nodes
may activate. ISRP owns the requirement catalog, selected assessment
requirements, work packages, responses, evidence, comments, determinations,
and final decisions.

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



## Gap, remediation, issue, and CAP ownership

A gap is first recorded as a FINDING under the assessment that discovered it. The finding links to the affected ASSESSMENT_REQUIREMENT records and to the impacted subjects. It is not copied onto the request.

The decision point is explicit:

~~~text
Finding
  -> FIX_IN_ASSESSMENT
       -> requestor changes solution
       -> reviewer validates
       -> finding may close
  -> REGISTER_NONCOMPLIANCE
       -> remediation case
       -> external issue registration
       -> corrective action plan
       -> external resolution signal
       -> ISRP validation
       -> finding may close
  -> RISK_EXCEPTION
       -> governed exception
       -> closure policy decides whether review may complete
~~~

REMEDIATION_CASE is the stable internal coordination aggregate. It can group one or more findings, including findings from separate assessments when they share one corrective program. ISSUE_REFERENCE and CORRECTIVE_ACTION_PLAN belong to the remediation case.

The request relationship is derived:

~~~text
ISRP_REQUEST
  -> ISRP_ASSESSMENT
       -> FINDING
            -> REMEDIATION_CASE_FINDING
                 -> REMEDIATION_CASE
                      -> ISSUE_REFERENCE
                      -> CORRECTIVE_ACTION_PLAN
~~~

This avoids competing ownership at request and assessment level while still supporting request dashboards and closure checks.

### External issue integration

Outbound registration is asynchronous:

~~~text
ISRP transaction
  -> save finding/remediation decision
  -> engine.record_event(..., "NONCOMPLIANCE_REGISTRATION_REQUESTED")
       writes the shared log row and its outbox row together
  -> commit
  -> connector creates or locates external issue idempotently
~~~

Inbound updates are also idempotent:

~~~text
Issue-management event or reconciliation poll
  -> engine.record_inbox_event(...)   the shared inbox, deduplicated
  -> correlate connector + provider event ID
  -> update ISSUE_REFERENCE observed state
  -> create ISRP validation work when externally resolved
  -> emit OUTBOX_EVENT for projections/workflow
~~~

The issue-management platform remains authoritative for its issue and plan execution state. ISRP retains the external identifiers, synchronized summary, timestamps, and audit trail. External RESOLVED never closes a finding automatically; an authorized reviewer must validate the corrective result against the requirement and evidence.

## RDBMS-first status projections

The production design keeps authoritative ISRP records, embedded Flow records,
outbox events, and request/assessment status projections in the same Oracle
database. Local development and contract verification use SQLite. A separate
NoSQL or search store is not required, and PostgreSQL is not part of the
initial production path.

Lifecycle status is authoritative and changes only through an authorized FSM transition. Operational summaries are derived:

- lifecycle_status: explicit request or assessment state
- primary_phase and active phases: DAG execution summary
- attention_status and reason: derived action or blocker
- progress counts: derived child completion
- compliance, evidence, finding, remediation, issue, CAP, and validation-pending counts: derived ISRP summary

~~~text
Child business change
  -> authoritative history and current pointer
  -> record_event writes the shared log row and its outbox row
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
- ISRP business updates, Flow execution updates, shared log entries, and shared
  outbox records commit in the same host-owned database transaction.
- Consumers handle events idempotently.
- External issue creation and updates never run inside the primary business transaction.
- Outbound effects and inbound provider events use the outbox and inbox shared
  with Flow, written through the engine. ISRP keeps no parallel set.
- Connector calls and inbound event handling are idempotent and reconcilable.
- External issue resolution creates validation work and never closes a finding by itself.
- Long-running work uses durable timers and retry policies.
