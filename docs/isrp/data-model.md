# ISRP Data Model Plan

## Storage recommendation

Use an RDBMS as the authoritative store. PostgreSQL is the recommended first implementation because of its transactional model, JSONB support, indexing, operational ecosystem, and development speed.

Support Oracle with a separate persistence adapter and dialect-specific migrations. Do not reduce the logical model to the lowest common SQL feature set.

Use NoSQL, search, reporting, or vector stores only as derived projections. The authoritative model requires transactions, uniqueness, referential integrity, immutable submission history, workflow joins, assignments, and reliable outbox processing.

## Identifier and portability conventions

- Generate identifiers in the application.
- Prefer sortable ULIDs stored as CHAR(26) for simple PostgreSQL and Oracle portability.
- Store state and type values as bounded strings, not database-native enums.
- Store timestamps in UTC.
- Use optimistic revision columns on mutable aggregates.
- Keep business rules in services rather than triggers or stored procedures.
- Use adapter-specific implementations for JSON queries, locking, queue claiming, and migrations.
- Map flexible metadata to PostgreSQL JSONB and an Oracle JSON-capable column.
- Keep frequently filtered, joined, constrained, or authorized fields as normal columns.

## Core relationships

~~~text
ISRP_REQUEST 1 ----- many ISRP_ASSESSMENT
ISRP_REQUEST 1 ----- many REQUEST_SUBJECT
SUBJECT_REFERENCE 1 ----- many REQUEST_SUBJECT

ISRP_ASSESSMENT 1 ----- many ASSESSMENT_SUBJECT
SUBJECT_REFERENCE 1 ----- many ASSESSMENT_SUBJECT

ISRP_ASSESSMENT 1 ----- many ASSESSMENT_REQUIREMENT
REQUIREMENT_VERSION 1 ----- many ASSESSMENT_REQUIREMENT

ASSESSMENT_REQUIREMENT 1 ----- many RESPONSE_SUBMISSION
ASSESSMENT_REQUIREMENT 1 ----- zero/one active RESPONSE_DRAFT
RESPONSE_SUBMISSION 1 ----- many RESPONDER_ASSERTION
RESPONSE_SUBMISSION 1 ----- many REVIEWER_DETERMINATION
ASSESSMENT_REQUIREMENT 1 ----- many FINAL_DECISION
RESPONSE_SUBMISSION 1 ----- many FEEDBACK
ASSESSMENT_REQUIREMENT many ----- many WORK_PACKAGE

ISRP_REQUEST 1 ----- one request WORKFLOW_INSTANCE reference
ISRP_ASSESSMENT 1 ----- one assessment WORKFLOW_INSTANCE reference
~~~

An assessment stores request_id directly. A request-assessment link table is unnecessary unless assessments can be shared across requests.

## ISRP request

ISRP_REQUEST:

| Field | Purpose |
|---|---|
| request_id | Internal identifier |
| request_number | Human-readable business key |
| title, description | Current request metadata |
| trigger_type | NEW, MATERIAL_CHANGE, or PERIODIC |
| requestor_id, requestor_org_id | Accountability |
| lifecycle_status | Request FSM state |
| attention_status | Derived action/blocker state |
| current_phase | Derived phase summary |
| workflow_instance_id | Opaque workflow reference |
| metadata_json | Less stable extension data |
| revision | Optimistic locking |
| created/updated/submitted/closed timestamps | Lifecycle audit support |

Current metadata is mutable. Audit events preserve accepted changes.

## Subjects and scope

SUBJECT_REFERENCE represents an external system-of-record object:

| Field | Purpose |
|---|---|
| subject_id | Internal reference |
| subject_type | APPLICATION, TECHNOLOGY, VENDOR, SAAS_PRODUCT, HARDWARE, SOFTWARE_PRODUCT, or SERVICE |
| source_system | Authoritative catalog name |
| source_identifier | Application ID, technology catalog ID, or vendor ID |
| display_name | Cached display value |
| metadata_json | Non-authoritative snapshot |
| active_flag | Reference status |

Required uniqueness:

~~~text
UNIQUE(source_system, subject_type, source_identifier)
~~~

REQUEST_SUBJECT identifies subjects considered during intake:

| Field | Purpose |
|---|---|
| request_subject_id | Identifier |
| request_id, subject_id | Relationship |
| scope_role | PRIMARY, SUPPORTING, ON_PREM_COMPONENT, INTEGRATION, DEPENDENCY, or HOSTING_PLATFORM |
| scope_reason | Intake explanation |
| included_flag | Current inclusion decision |

ASSESSMENT_SUBJECT narrows request scope for a particular assessment:

| Field | Purpose |
|---|---|
| assessment_subject_id | Identifier |
| assessment_id, subject_id | Relationship |
| scope_role | Role inside this assessment |
| included_flag | Applicability |
| applicability_reason | Decision rationale |

This model supports applications without technology, technologies without applications, and products such as Zscaler or CrowdStrike represented by records in multiple source systems.

## Duplicate and overlap controls

Duplicate detection combines exact constraints with advisory similarity checks.

Exact checks:

- Idempotency key uniqueness for request creation.
- Unique source-system subject identity.
- Configurable active-assessment uniqueness key.
- Unique assessment requirement per assessment and requirement version.

Advisory checks compare:

- Subject overlap
- Assessment type
- Trigger type
- Active or recently completed time window
- Requestor organization
- Material-change reference
- Product/vendor name aliases

Store a normalized scope fingerprint for fast candidate lookup, but do not rely on the fingerprint alone. A user can link to an existing request, continue with a documented override, or create a separate request when scope materially differs.

## ISRP assessment

ISRP_ASSESSMENT:

| Field | Purpose |
|---|---|
| assessment_id, assessment_number | Identity |
| request_id | Owning request |
| assessment_type | EXTERNAL, INTERNAL_APPLICATION, INFRASTRUCTURE, or configured extension |
| owning_org_id, assessment_lead_id | Accountability |
| lifecycle_status | Assessment FSM state |
| attention_status | Derived action state |
| current_phase | DAG progress summary |
| workflow_instance_id | Opaque workflow reference |
| workflow_definition_version | Reproducibility |
| metadata_json | Flexible assessment metadata |
| revision | Optimistic locking |
| started, target, completed timestamps | Planning and audit |

ASSESSMENT_RELATIONSHIP optionally records DEPENDS_ON, BLOCKS, SUPPLEMENTS, SHARES_EVIDENCE_WITH, or SUPERSEDES relationships.

## Requirement catalog and assessment snapshot

SECURITY_DOMAIN groups organizational policy domains such as Authentication Domain Standards. IS_REQUIREMENT stores the stable requirement identity and code. IS_REQUIREMENT_VERSION stores immutable published text, guidance, expected evidence, response schema, and effective dates.

REQUIREMENT_SET and immutable REQUIREMENT_SET_VERSION records define which requirement versions apply to an assessment type. REQUIREMENT_SET_MEMBER supports applicability rules and default assignment roles. Assessment creation may combine a baseline set with flow, subject, classification, or approved manual overlays.

ASSESSMENT_REQUIREMENT references the exact requirement version selected for the assessment and records:

- selection source and required flag
- applicability status and rationale
- response lifecycle status
- current assertion, determination, and final-decision pointers
- current compliance, implementation-currency, and evidence-freshness projections
- assessment-specific configuration and optimistic revision

This prevents a catalog edit from changing the meaning of an active or completed assessment. Changes to launched scope require an authorized, audited amendment.

## Work packages and assignments

REQUIREMENT_WORK_PACKAGE groups selected assessment requirements for a particular responder, reviewer, SME, or approver activity. WORK_PACKAGE_REQUIREMENT associates a subset of requirements and the actor role. WORK_PACKAGE_ASSIGNMENT targets a user, group, or organization.

A requirement may appear in multiple packages with distinct roles. The work package links to a Flow step instance and has its own assignment FSM, while ASSESSMENT_REQUIREMENT remains the authoritative compliance record.

## Response and decision lifecycle

REQUIREMENT_RESPONSE_DRAFT contains one active editable draft per response cycle:

- assessment_requirement_id
- response data
- claimed outcome, implementation currency, and evidence freshness
- revision
- updated_by and updated_at

REQUIREMENT_RESPONSE_SUBMISSION is immutable and numbered. It captures the exact response, evidence-version links, citations, actor, role, organization, timestamp, and superseded submission.

RESPONDER_ASSERTION records what the responder claims. REVIEWER_DETERMINATION records one or more SME conclusions without altering the responder assertion. REQUIREMENT_FINAL_DECISION records the authoritative disposition and may be created only by a configured decision authority.

The three independent dimensions are:

- compliance outcome: NOT_ASSESSED, MET, PARTIALLY_MET, NOT_MET, or NOT_APPLICABLE
- implementation currency: UNKNOWN, CURRENT, OUTDATED, PLANNED_REPLACEMENT, or DECOMMISSIONED
- evidence freshness: NOT_PROVIDED, CURRENT, STALE, EXPIRED, or NOT_REQUIRED

Every change creates a superseding immutable record with mandatory justification. Current values on ASSESSMENT_REQUIREMENT are query projections.

REQUIREMENT_FEEDBACK and REQUIREMENT_COMMENT are append-only. Corrections create linked superseding comments rather than editing prior commentary. See [Flow to IS Requirements](requirements-catalog.md) for detailed authority and transition rules.

## Evidence, findings, and issues

Store large files in controlled object/document storage. EVIDENCE_ITEM represents a logical attachment and points to a current immutable EVIDENCE_VERSION. Each version stores its location, content hash, original filename, classification, ownership, scan status, replacement reason, and timestamps. Replacing evidence creates a new version and never overwrites the original object.

EVIDENCE_CITATION links an assessment requirement to an exact evidence version and stores the citation blurb, page or section locator, author, and timestamp. Replacing evidence flags dependent citations for revalidation while preserving the citations used by historical submissions and decisions.

FINDING stores the ISRP-owned noncompliance or concern. ISSUE_REFERENCE stores the external issue-management identifier, connector name, synchronization state, last observed status, and timestamps.

Issue creation uses the transactional outbox and an idempotency key so retries cannot create duplicate issues.

## Generic workflow persistence

The generic service maintains:

- WORKFLOW_DEFINITION and immutable WORKFLOW_DEFINITION_VERSION
- NODE_DEFINITION and EDGE_DEFINITION
- FSM_DEFINITION, FSM_STATE, and FSM_TRANSITION
- WORKFLOW_INSTANCE and STEP_INSTANCE
- STEP_TRANSITION_HISTORY
- WORK_ITEM and WORK_ASSIGNMENT
- TIMER
- COMMAND_DEDUPLICATION
- AUDIT_EVENT
- OUTBOX_EVENT

Workflow records use opaque business_type, business_key, and correlation_id values instead of foreign keys into the ISRP database.

## Request and assessment status projections

Lifecycle state remains on ISRP_REQUEST and ISRP_ASSESSMENT and changes only through their FSMs. Append-only REQUEST_STATUS_HISTORY and ASSESSMENT_STATUS_HISTORY record explicit transitions, reasons, actors, workflow correlation, and aggregate revisions.

Derived summaries live in rebuildable relational tables:

- REQUEST_STATUS_PROJECTION
- ASSESSMENT_STATUS_PROJECTION
- optionally ASSESSMENT_ACTIVE_PHASE for normalized parallel phase state

Projection fields include lifecycle status for convenient reads, primary and active phases, attention status and reason, required/completed counts, requirement outcome counts, evidence and implementation attention counts, work-package queues, findings, issues, source version, and projection timestamp.

The status projector consumes OUTBOX_EVENT records, updates the assessment projection, then updates the parent request projection. PROCESSED_EVENT provides consumer-level idempotency. Recalculation is available as an administrative repair operation.

When ISRP and Flow use separate databases, parent summaries are eventually consistent. APIs expose projection version and timestamp where useful. Closure, cancellation, reopening, and other controlled transitions revalidate authoritative records instead of relying solely on a projection. See [RDBMS status projections](status-projections.md).

## NoSQL and search projections

A request projection may embed subject and assessment summaries for fast screens:

~~~json
{
  "requestId": "ISR-100",
  "status": "ASSESSMENTS_IN_PROGRESS",
  "attention": "ACTION_REQUIRED",
  "progress": {"completed": 2, "required": 3},
  "subjects": [],
  "assessmentSummaries": []
}
~~~

Do not treat the embedded document as the source for workflow transitions or immutable response history. Build it from outbox or change events, and make it disposable and rebuildable.

## Multi-store consistency

Use this sequence:

1. Validate the command and revision.
2. Update authoritative relational records.
3. Append audit and outbox records in the same transaction.
4. Commit.
5. Deliver the event asynchronously.
6. Update NoSQL, search, reporting, AI, or external-system projections idempotently.

Do not attempt a distributed transaction between the RDBMS, document database, search engine, and issue-management system.
