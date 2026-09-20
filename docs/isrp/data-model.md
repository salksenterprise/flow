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
RESPONSE_SUBMISSION 1 ----- many FEEDBACK
ASSESSMENT_REQUIREMENT 1 ----- many DECISION

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

REQUIREMENT stores the stable identity and code. REQUIREMENT_VERSION stores immutable text, guidance, response schema, and effective dates.

ASSESSMENT_REQUIREMENT references the exact requirement version selected for the assessment and records:

- applicability status and rationale
- assigned reviewer or owning group
- current decision status
- current submitted response number
- assessment-specific configuration

This prevents a catalog edit from changing the meaning of an active or completed assessment.

## Response lifecycle

REQUIREMENT_RESPONSE_DRAFT contains one active editable draft per response cycle:

- assessment_requirement_id
- response_json
- revision
- updated_by and updated_at

REQUIREMENT_RESPONSE_SUBMISSION is immutable:

- submission_id
- assessment_requirement_id
- submission_number
- response_json
- submitted_by and submitted_at
- supersedes_submission_id

REQUIREMENT_FEEDBACK references a specific submission and records feedback type, comment, author, and timestamp.

REQUIREMENT_DECISION references the evaluated submission and records MET, PARTIALLY_MET, NOT_MET, NOT_APPLICABLE, or ACCEPTED_EXCEPTION with rationale and decision authority.

## Evidence, findings, and issues

Store large files in controlled object/document storage. EVIDENCE_REFERENCE stores location, content hash, classification, ownership, timestamps, and optional assessment-requirement association.

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

## Parent projections

Store lifecycle state separately from derived summaries:

- lifecycle_status
- current_phase
- attention_status
- attention_reason
- completed_count
- required_count
- projection_version

Parent projections update from child events. Recalculation must be idempotent and available as an administrative repair operation.

When ISRP and workflow use separate databases, parent summaries are eventually consistent. APIs should expose projection timestamps or versions where operationally useful.

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
