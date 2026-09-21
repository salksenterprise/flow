# Canonical ISRP Data Model

## Purpose and authority

This is the single canonical logical data model for the Information Security Review Process (ISRP) and its integration with the generic Flow workflow service.

Other ISRP documents describe behavior, workflow, delivery phases, or visual examples. When an entity name, relationship, field, or constraint differs, this document is authoritative.

The model covers:

- requests and assessments
- applications, technologies, vendors, products, and other subjects
- duplicate and overlap detection
- generic Flow references
- lifecycle state and transition history
- security domains, requirements, versions, and requirement sets
- assessment requirement snapshots
- requirement work packages and assignments
- responses, assertions, determinations, and final decisions
- higher-level evidence, immutable evidence versions, and citations
- append-only comments and justifications
- findings, affected subjects, remediation cases, issues, CAPs, exceptions, validation, and closure
- audit, idempotency, outbox, and processed events
- request and assessment status projections
- PostgreSQL and Oracle portability
- current deterministic scope and future AI extensions

## Current and future phase boundary

### Current phase

The current ISRP implementation is deterministic and non-AI.

Supported execution modes:

~~~text
HUMAN
AUTOMATION
~~~

Current automation includes rules, validation, routing, timers, retries, projection, and integration. Security meaning is supplied or confirmed by authorized humans.

### Future phase

Reserved execution modes:

~~~text
AI_ASSISTED_HUMAN
AI_AUTOMATED_SUPERVISED
~~~

Future AI output is always a proposal. It is not an authoritative responder assertion, reviewer determination, final decision, finding, exception, or closure authorization. An authorized human disposition followed by a deterministic apply command creates authoritative changes.

## Storage strategy

Use PostgreSQL as the initial authoritative store. Support Oracle through separate repository adapters and dialect-specific migrations.

Use NoSQL, search, reporting, and vector stores only as derived, disposable, rebuildable projections. They are never the source of truth for transitions, authorization, decisions, evidence versions, or audit.

Large evidence files live in controlled object/document storage. The RDBMS stores their immutable version metadata, hashes, classification, authorization context, and storage references.

## Portability conventions

- Generate identifiers in application code.
- Prefer sortable ULIDs stored as CHAR(26).
- Store timestamps in UTC with timezone-aware application types.
- Store states and types as bounded strings rather than database-native enums.
- Use optimistic revision columns on mutable aggregates.
- Keep business rules in services rather than triggers or stored procedures.
- Map flexible data to PostgreSQL JSONB and an Oracle JSON-capable column.
- Keep frequently joined, filtered, constrained, authorized, or reported values in normal columns.
- Use separate adapters for JSON operators, locking, outbox claiming, and migrations.
- Use explicit bridge tables instead of unconstrained entity_type/entity_id associations where referential integrity matters.

## Ownership boundaries

### ISRP service owns

- requests and assessments
- subjects and scope
- requirement catalog and requirement sets
- assessment requirement snapshots
- work-package business scope
- responses and evidence
- assertions, determinations, and decisions
- findings, remediation cases, CAPs, external issue references, and exceptions
- lifecycle transition history
- audit and business outbox events
- request and assessment status projections

### Flow service owns

- workflow definitions and immutable versions
- DAG node and edge definitions
- FSM definitions and transitions
- workflow and step instances
- work items and assignment mechanics
- timers, retries, and execution transitions
- Flow audit and Flow outbox events

ISRP stores Flow identifiers as opaque references. When deployed as separate services, there are no cross-database foreign keys.

## High-level relationship view

~~~text
ISRP_REQUEST
  |
  +--< REQUEST_SUBJECT >-- SUBJECT_REFERENCE
  |
  +--< ISRP_ASSESSMENT
  |      |
  |      +--< ASSESSMENT_SUBJECT >-- SUBJECT_REFERENCE
  |      |
  |      +--< ASSESSMENT_REQUIREMENT
  |      |      |
  |      |      +-- one active RESPONSE_DRAFT
  |      |      +--< RESPONSE_SUBMISSION
  |      |      |      +--< RESPONDER_ASSERTION
  |      |      |      +--< REVIEWER_DETERMINATION
  |      |      |
  |      |      +--< REQUIREMENT_FINAL_DECISION
  |      |      +--< REQUIREMENT_COMMENT
  |      |      +--< EVIDENCE_CITATION >-- EVIDENCE_VERSION
  |      |
  |      +--< REQUIREMENT_WORK_PACKAGE
  |      |      +--< WORK_PACKAGE_REQUIREMENT
  |      |      +--< WORK_PACKAGE_ASSIGNMENT
  |      |
  |      +--< FINDING
  |             +--< FINDING_REQUIREMENT
  |             +--< FINDING_SUBJECT
  |             +--< REMEDIATION_CASE_FINDING >-- REMEDIATION_CASE
  |                                                   +--< ISSUE_REFERENCE
  |                                                   +--< CORRECTIVE_ACTION_PLAN
  |                                                          +--< CAP_ACTION_ITEM
  |
  +--< REQUEST_EVIDENCE >-- EVIDENCE_ITEM
                              |
                              +--< EVIDENCE_VERSION

SECURITY_DOMAIN
  +--< IS_REQUIREMENT
         +--< IS_REQUIREMENT_VERSION
                  ^
                  |
REQUIREMENT_SET  |
  +--< REQUIREMENT_SET_VERSION
         +--< REQUIREMENT_SET_MEMBER

Flow references:
ISRP_REQUEST.workflow_instance_id
ISRP_ASSESSMENT.workflow_instance_id
REQUIREMENT_WORK_PACKAGE.step_instance_id
~~~

# Part I: Identity, organization, and subjects

## External identity references

ISRP normally references an enterprise identity provider or directory rather than duplicating user identities.

### ACTOR_REFERENCE

~~~text
actor_id
actor_type                 USER | SERVICE
source_system
source_identifier
display_name
primary_org_id
active_flag
last_synchronized_at
~~~

Constraint:

~~~text
UNIQUE(source_system, actor_type, source_identifier)
~~~

### ORGANIZATION_REFERENCE

~~~text
organization_id
source_system
source_identifier
name
organization_type
parent_organization_id
active_flag
last_synchronized_at
~~~

## Subject registry

A subject is something being reviewed or providing context to a review.

### SUBJECT_REFERENCE

~~~text
subject_id
subject_type
source_system
source_identifier
display_name
description
metadata_json
active_flag
created_at
updated_at
~~~

Subject types include:

~~~text
APPLICATION
TECHNOLOGY
VENDOR
SAAS_PRODUCT
SOFTWARE_PRODUCT
HARDWARE
SERVICE
PLATFORM
OTHER
~~~

Constraint:

~~~text
UNIQUE(source_system, subject_type, source_identifier)
~~~

This permits:

- application without technology
- technology without application
- infrastructure assessment without application
- a product such as Zscaler or CrowdStrike represented by records in several systems of record

The registry does not imply a permanent application-to-technology mapping.

# Part II: Request aggregate

## ISRP_REQUEST

The request is the business container for one or more assessments.

~~~text
request_id
request_number
title
description
trigger_type
requestor_id
requestor_org_id
lifecycle_status
workflow_instance_id
metadata_json
scope_fingerprint
revision
created_at
created_by
updated_at
updated_by
submitted_at
closed_at
~~~

Trigger types:

~~~text
NEW
MATERIAL_CHANGE
PERIODIC
~~~

Lifecycle states:

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

Constraints:

~~~text
UNIQUE(request_number)
revision >= 1
CLOSED requires closed_at
~~~

Metadata is mutable current state protected by optimistic locking. Audit events preserve accepted changes.

## REQUEST_SUBJECT

Identifies subjects considered during request intake.

~~~text
request_subject_id
request_id
subject_id
scope_role
scope_reason
included_flag
created_at
created_by
removed_at
removed_by
removal_reason
~~~

Scope roles include:

~~~text
PRIMARY
SUPPORTING
ON_PREM_COMPONENT
HOSTING_PLATFORM
INTEGRATION
DEPENDENCY
~~~

Constraint:

~~~text
UNIQUE(request_id, subject_id, scope_role)
~~~

## REQUEST_STATUS_HISTORY

Append-only explicit request lifecycle transitions.

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
correlation_id
~~~

## Duplicate and overlap review

### REQUEST_DUPLICATE_MATCH

Stores candidate matches and their disposition.

~~~text
duplicate_match_id
request_id
candidate_request_id
match_type
match_score
matching_subjects_json
matching_assessment_types_json
status
reviewed_by
reviewed_at
resolution
resolution_reason
created_at
~~~

Match types:

~~~text
EXACT_IDEMPOTENCY
EXACT_SCOPE
SUBJECT_OVERLAP
ACTIVE_ASSESSMENT_OVERLAP
RECENT_REVIEW_OVERLAP
NAME_ALIAS
~~~

Resolution values:

~~~text
LINK_EXISTING
CANCEL_NEW
PROCEED_DISTINCT_SCOPE
PROCEED_OVERRIDE
NOT_DUPLICATE
~~~

Exact duplicate prevention uses constraints and idempotency keys. Similarity matches remain advisory and require a captured disposition.

# Part III: Assessment aggregate

## ISRP_ASSESSMENT

An independently owned unit of review.

~~~text
assessment_id
assessment_number
request_id
assessment_type
title
description
owning_org_id
assessment_lead_id
lifecycle_status
workflow_instance_id
workflow_definition_version
metadata_json
revision
started_at
target_at
completed_at
created_at
created_by
updated_at
updated_by
~~~

Assessment types initially include:

~~~text
EXTERNAL
INTERNAL_APPLICATION
INFRASTRUCTURE
~~~

Lifecycle states:

~~~text
DRAFT
PLANNED
IN_PROGRESS
READY_TO_COMPLETE
COMPLETED
ON_HOLD
CANCELLED
~~~

Constraints:

~~~text
UNIQUE(assessment_number)
request_id NOT NULL
revision >= 1
COMPLETED requires completed_at
~~~

An assessment belongs to exactly one request. A request-assessment bridge table is unnecessary unless a future approved requirement permits shared assessments.

## ASSESSMENT_SUBJECT

Narrows request scope for a particular assessment.

~~~text
assessment_subject_id
assessment_id
subject_id
scope_role
included_flag
applicability_reason
created_at
created_by
removed_at
removed_by
removal_reason
~~~

Constraint:

~~~text
UNIQUE(assessment_id, subject_id, scope_role)
~~~

## ASSESSMENT_RELATIONSHIP

~~~text
relationship_id
from_assessment_id
to_assessment_id
relationship_type
created_at
created_by
~~~

Relationship types:

~~~text
DEPENDS_ON
BLOCKS
SUPPLEMENTS
SHARES_EVIDENCE_WITH
SUPERSEDES
~~~

## ASSESSMENT_STATUS_HISTORY

Append-only explicit assessment lifecycle transitions.

~~~text
status_history_id
assessment_id
previous_status
new_status
transition_name
reason
changed_by
changed_at
workflow_instance_id
step_instance_id
assessment_revision
correlation_id
~~~

# Part IV: Generic Flow model and ISRP bindings

The Flow service maintains its own canonical schema. The following list records the integration boundary.

## Flow definition entities

~~~text
WORKFLOW_DEFINITION
WORKFLOW_DEFINITION_VERSION
WORKFLOW_NODE_DEFINITION
WORKFLOW_EDGE_DEFINITION
FSM_DEFINITION
FSM_STATE
FSM_TRANSITION
~~~

Definitions are immutable after publication.

## Flow runtime entities

~~~text
WORKFLOW_INSTANCE
STEP_INSTANCE
STEP_TRANSITION_HISTORY
WORK_ITEM
WORK_ASSIGNMENT
TIMER
COMMAND_DEDUPLICATION
FLOW_AUDIT_EVENT
FLOW_OUTBOX_EVENT
~~~

## ISRP bindings

~~~text
ISRP_REQUEST.workflow_instance_id
ISRP_ASSESSMENT.workflow_instance_id
REQUIREMENT_WORK_PACKAGE.step_instance_id
REQUIREMENT_WORK_PACKAGE.work_item_id
~~~

Flow uses:

~~~text
business_type
business_key
correlation_id
~~~

Example:

~~~text
business_type = ISRP_ASSESSMENT
business_key = ASMT-1002
correlation_id = ISR-100
~~~

## Execution modes

Current:

~~~text
HUMAN
AUTOMATION
~~~

Future reserved:

~~~text
AI_ASSISTED_HUMAN
AI_AUTOMATED_SUPERVISED
~~~

Current ISRP workflow publication rejects future AI modes.

# Part V: Requirement catalog

## SECURITY_DOMAIN

~~~text
domain_id
domain_code
name
description
owner_org_id
active_flag
created_at
updated_at
~~~

Example:

~~~text
domain_code = ADS
name = Authentication Domain Standards
~~~

Constraint:

~~~text
UNIQUE(domain_code)
~~~

## IS_REQUIREMENT

Stable requirement identity.

~~~text
requirement_id
requirement_code
domain_id
title
owner_org_id
lifecycle_status
created_at
created_by
updated_at
updated_by
~~~

Lifecycle:

~~~text
DRAFT
PUBLISHED
RETIRED
~~~

Constraint:

~~~text
UNIQUE(requirement_code)
~~~

## IS_REQUIREMENT_VERSION

Immutable published content.

~~~text
requirement_version_id
requirement_id
version_number
requirement_text
guidance_text
expected_evidence_text
response_schema_json
effective_from
effective_to
published_at
published_by
content_hash
~~~

Constraints:

~~~text
UNIQUE(requirement_id, version_number)
published versions are immutable
effective_to >= effective_from when present
~~~

## REQUIREMENT_SET

Stable reusable profile identity.

~~~text
requirement_set_id
requirement_set_code
name
description
assessment_type
owner_org_id
lifecycle_status
created_at
updated_at
~~~

Examples:

~~~text
EXTERNAL_SAAS_BASELINE
EXTERNAL_ON_PREM_OVERLAY
INFRASTRUCTURE_SOFTWARE_BASELINE
INFRASTRUCTURE_HARDWARE_BASELINE
INTERNAL_APPLICATION_BASELINE
RESTRICTED_DATA_OVERLAY
ADS_FEDERATION_OVERLAY
~~~

## REQUIREMENT_SET_VERSION

~~~text
requirement_set_version_id
requirement_set_id
version_number
status
published_at
published_by
content_hash
~~~

Constraint:

~~~text
UNIQUE(requirement_set_id, version_number)
published versions are immutable
~~~

## REQUIREMENT_SET_MEMBER

~~~text
set_member_id
requirement_set_version_id
requirement_version_id
required_flag
applicability_rule_json
default_assignment_role
display_order
~~~

Constraint:

~~~text
UNIQUE(requirement_set_version_id, requirement_version_id)
~~~

# Part VI: Assessment requirement snapshot

## ASSESSMENT_REQUIREMENT

The authoritative requirement instance for one assessment.

~~~text
assessment_requirement_id
assessment_id
requirement_version_id
selection_source
selection_rule_id
selection_rule_version
selection_input_snapshot_json
required_flag
applicability_status
applicability_reason
response_status

current_assertion_id
current_determination_id
current_final_decision_id

current_final_outcome
current_implementation_currency
current_evidence_freshness

attention_status
revision
selected_at
selected_by
decided_at
~~~

Applicability:

~~~text
PENDING
APPLICABLE
CONDITIONALLY_APPLICABLE
NOT_APPLICABLE
INSUFFICIENT_INFORMATION
~~~

Response status:

~~~text
NOT_STARTED
DRAFT
SUBMITTED
IN_REVIEW
CLARIFICATION_REQUIRED
RESUBMITTED
DECIDED
WITHDRAWN
CANCELLED
~~~

Constraints:

~~~text
UNIQUE(assessment_id, requirement_version_id)
revision >= 1
current pointers must belong to this assessment requirement
~~~

Catalog changes never alter existing assessment requirement snapshots.

## ASSESSMENT_REQUIREMENT_CHANGE

Append-only post-launch scope or applicability changes.

~~~text
requirement_change_id
assessment_requirement_id
change_type
previous_value_json
new_value_json
reason
changed_by
changed_at
assessment_revision
~~~

Change types:

~~~text
ADDED_TO_SCOPE
REMOVED_FROM_SCOPE
REINSTATED
APPLICABILITY_CHANGED
REQUIRED_FLAG_CHANGED
~~~

# Part VII: Work packages and assignment

## REQUIREMENT_WORK_PACKAGE

A distribution unit linked to a Flow step.

~~~text
work_package_id
assessment_id
package_type
title
description
owning_org_id
status
step_instance_id
work_item_id
due_at
revision
created_at
created_by
completed_at
~~~

Package types:

~~~text
RESPONDER
REVIEWER
SME_REVIEWER
APPROVER
VALIDATION
~~~

Status:

~~~text
PENDING
ASSIGNED
IN_PROGRESS
SUBMITTED
IN_REVIEW
CLARIFICATION_REQUIRED
RESUBMITTED
COMPLETED
CANCELLED
~~~

## WORK_PACKAGE_REQUIREMENT

~~~text
work_package_requirement_id
work_package_id
assessment_requirement_id
assignment_role
required_for_package_completion
display_order
added_at
added_by
removed_at
removed_by
removal_reason
~~~

Constraint:

~~~text
UNIQUE(work_package_id, assessment_requirement_id, assignment_role)
~~~

The same assessment requirement may appear in several packages with different roles.

## WORK_PACKAGE_ASSIGNMENT

~~~text
assignment_id
work_package_id
assignee_type
user_id
group_id
organization_id
role
status
assigned_at
assigned_by
accepted_at
completed_at
~~~

Assignee types:

~~~text
USER
GROUP
ORGANIZATION
~~~

Roles:

~~~text
RESPONDER
REVIEWER
SME_REVIEWER
APPROVER
OBSERVER
~~~

Exactly one of user_id, group_id, or organization_id is populated according to assignee_type.

# Part VIII: Evidence

## EVIDENCE_ITEM

Logical attachment identity.

~~~text
evidence_id
title
description
evidence_type
classification
owner_org_id
status
current_version_id
valid_from
valid_until
metadata_json
created_at
created_by
updated_at
updated_by
~~~

Evidence types include:

~~~text
TECHNICAL_DESIGN
ARCHITECTURE_DIAGRAM
DATA_FLOW
CONFIGURATION_EXPORT
TEST_RESULT
PENETRATION_TEST
SOC2_REPORT
ISO_CERTIFICATE
POLICY
PROCEDURE
SCREENSHOT
VENDOR_RESPONSE
OTHER
~~~

## EVIDENCE_VERSION

Immutable uploaded object.

~~~text
evidence_version_id
evidence_id
version_number
storage_reference
original_filename
media_type
file_size
content_hash
uploaded_by
uploaded_at
supersedes_version_id
replacement_reason
malware_scan_status
processing_status
~~~

Constraints:

~~~text
UNIQUE(evidence_id, version_number)
UNIQUE(content_hash, evidence_id)
uploaded versions are immutable
current_version_id must belong to the evidence item
~~~

Replacement creates a new version and never overwrites the original object.

## Evidence scope links

### REQUEST_EVIDENCE

~~~text
request_evidence_id
request_id
evidence_id
scope_role
shared_with_assessments_flag
created_at
created_by
~~~

### ASSESSMENT_EVIDENCE

~~~text
assessment_evidence_id
assessment_id
evidence_id
scope_role
created_at
created_by
~~~

### SUBJECT_EVIDENCE

~~~text
subject_evidence_id
subject_id
evidence_id
scope_role
reuse_policy
created_at
created_by
~~~

Each bridge has a uniqueness constraint on its owner and evidence item.

Evidence availability does not imply requirement applicability.

## EVIDENCE_COLLECTION

Groups evidence for intake, design, validation, or another workflow purpose.

~~~text
collection_id
request_id
assessment_id
title
purpose
status
created_at
created_by
~~~

At least one of request_id or assessment_id is present.

## EVIDENCE_COLLECTION_ITEM

~~~text
collection_item_id
collection_id
evidence_id
required_flag
display_order
added_at
added_by
~~~

Constraint:

~~~text
UNIQUE(collection_id, evidence_id)
~~~

## EVIDENCE_CITATION

Links an exact evidence version to an assessment requirement.

~~~text
citation_id
assessment_requirement_id
evidence_version_id
submission_id
citation_blurb
page_number
section_reference
source_locator
verification_status
created_by
created_at
supersedes_citation_id
~~~

Current verification values:

~~~text
HUMAN_CREATED
HUMAN_VERIFIED
REVALIDATION_REQUIRED
REJECTED
~~~

Future reserved:

~~~text
AI_PROPOSED
~~~

Citations are immutable. Corrections create superseding citations. Evidence replacement preserves prior citations and flags relevant current citations for revalidation.

## Deterministic evidence processing

### EVIDENCE_PROCESSING_RUN

~~~text
processing_run_id
evidence_version_id
processor_type
processor_version
status
started_at
completed_at
attempt_count
error_code
error_details
~~~

Current processor types:

~~~text
FILE_VALIDATE
MALWARE_SCAN
HASH_VERIFY
NATIVE_TEXT_EXTRACT
PAGE_INDEX
EXACT_DUPLICATE_CHECK
~~~

Derived extracted text and page indexes may be stored in rebuildable projection tables. They are not authoritative evidence.

# Part IX: Responses and evaluation authority

## Three independent evaluation dimensions

### Compliance outcome

~~~text
NOT_ASSESSED
MET
PARTIALLY_MET
NOT_MET
NOT_APPLICABLE
~~~

### Implementation currency

~~~text
UNKNOWN
CURRENT
OUTDATED
PLANNED_REPLACEMENT
DECOMMISSIONED
~~~

### Evidence freshness

~~~text
NOT_PROVIDED
CURRENT
STALE
EXPIRED
NOT_REQUIRED
~~~

These dimensions are stored separately. A display label such as “Met — evidence is stale” is derived from MET plus STALE.

## REQUIREMENT_RESPONSE_DRAFT

One active editable draft per assessment requirement.

~~~text
draft_id
assessment_requirement_id
response_text
response_json
claimed_outcome
claimed_implementation_currency
claimed_evidence_freshness
revision
updated_by
updated_at
~~~

Constraint:

~~~text
UNIQUE active draft per assessment_requirement_id
revision >= 1
~~~

Draft edits overwrite current draft state with optimistic locking.

## REQUIREMENT_RESPONSE_SUBMISSION

Immutable numbered submission.

~~~text
submission_id
assessment_requirement_id
submission_number
response_text
response_json
claimed_outcome
claimed_implementation_currency
claimed_evidence_freshness
submitted_by
submitted_as_role
submitted_for_org_id
submitted_at
supersedes_submission_id
content_hash
~~~

Constraint:

~~~text
UNIQUE(assessment_requirement_id, submission_number)
submitted records are immutable
~~~

A submission snapshots exact evidence-version and citation links.

## SUBMISSION_CITATION

~~~text
submission_citation_id
submission_id
citation_id
evidence_version_id
~~~

Constraint:

~~~text
UNIQUE(submission_id, citation_id)
~~~

## RESPONDER_ASSERTION

What the responder claims.

~~~text
assertion_id
assessment_requirement_id
submission_id
claimed_outcome
implementation_currency
evidence_freshness
assertion_text
justification_text
asserted_by
asserted_as_role
asserted_for_org_id
asserted_at
supersedes_assertion_id
~~~

Assertions are immutable.

## REVIEWER_DETERMINATION

An assigned reviewer or SME conclusion.

~~~text
determination_id
assessment_requirement_id
submission_id
work_package_id
reviewer_role
determined_outcome
implementation_currency
evidence_freshness
determination_text
justification_text
determined_by
determined_for_org_id
determined_at
supersedes_determination_id
~~~

Several SME determinations may coexist. A reviewer never overwrites the responder assertion.

## REQUIREMENT_FINAL_DECISION

Authoritative disposition for reporting, findings, and closure.

~~~text
decision_id
assessment_requirement_id
based_on_submission_id
final_outcome
implementation_currency
evidence_freshness
decision_rationale
decided_by
decision_authority
decided_at
supersedes_decision_id
reopened_from_decision_id
~~~

Only configured decision authority can create a final decision. Decisions are immutable; changes create a superseding decision with mandatory rationale.

## REQUIREMENT_COMMENT

Append-only actor commentary and clarification.

~~~text
comment_id
assessment_requirement_id
submission_id
work_package_id
parent_comment_id
comment_type
comment_text
author_id
author_org_id
author_role
visibility
created_at
supersedes_comment_id
~~~

Comment types:

~~~text
GENERAL
CLARIFICATION_REQUEST
CLARIFICATION_RESPONSE
REVIEW_NOTE
DECISION_RATIONALE
EVIDENCE_NOTE
CORRECTION
SYSTEM_NOTE
~~~

Visibility:

~~~text
ALL_PARTICIPANTS
INTERNAL_REVIEWERS
ASSIGNED_ORGANIZATION
AUDIT_ONLY
~~~

Comments are never edited in place. A correction is a new linked comment. Structured reviewer feedback uses this entity with an appropriate comment type and submission reference.

## Justification invariant

Justification is mandatory for:

- outcome changes
- reviewer disagreement with responder
- implementation-currency changes
- stale or expired evidence classification
- evidence replacement
- not-applicable disposition
- final-decision change
- reopening a decided requirement
- exception acceptance
- post-launch scope change

The justification is stored on the immutable record that performs the change. Additional narrative is appended through REQUIREMENT_COMMENT.

# Part X: Findings, remediation, issues, CAPs, and exceptions

## Ownership rule

FINDING belongs to the ISRP_ASSESSMENT that discovered the gap. It may link to multiple affected assessment requirements and subjects. The parent request sees the finding through its assessment; no duplicate request-owned finding is created.

REMEDIATION_CASE is the coordination aggregate for treatment. It can group one or more findings, including findings from more than one assessment when a single corrective program is justified. ISSUE_REFERENCE and CORRECTIVE_ACTION_PLAN belong to the remediation case.

## FINDING

~~~text
finding_id
assessment_id
finding_number
title
description
severity
status
disposition
owner_org_id
owner_actor_id
identified_at
identified_by
target_date
resolved_at
validated_at
validated_by
closed_at
revision
~~~

Status:

~~~text
OPEN
AWAITING_DISPOSITION
FIX_IN_PROGRESS
PENDING_EXTERNAL_REGISTRATION
EXTERNAL_REMEDIATION
VALIDATION_PENDING
RESOLVED
CLOSED
CANCELLED
~~~

Disposition:

~~~text
UNDECIDED
FIX_IN_ASSESSMENT
REGISTER_NONCOMPLIANCE
RISK_EXCEPTION
NOT_A_FINDING
~~~

A finding cannot become RESOLVED or CLOSED merely because an external issue is resolved. ISRP validation must confirm the corrective result against current evidence and the linked requirements.

## FINDING_REQUIREMENT

~~~text
finding_requirement_id
finding_id
assessment_requirement_id
relationship_type
created_at
created_by
~~~

Constraint:

~~~text
UNIQUE(finding_id, assessment_requirement_id)
~~~

## FINDING_SUBJECT

Links the finding to the impacted application, technology, vendor, product, or other scoped subject.

~~~text
finding_subject_id
finding_id
subject_id
relationship_type
created_at
created_by
~~~

Constraint:

~~~text
UNIQUE(finding_id, subject_id, relationship_type)
~~~

## REMEDIATION_CASE

Internal coordination record for treating one or more findings.

~~~text
remediation_case_id
case_number
title
description
status
treatment_strategy
owner_org_id
owner_actor_id
target_date
validation_status
validation_required_flag
opened_at
resolved_at
closed_at
revision
~~~

Status:

~~~text
OPEN
PLANNING
IN_PROGRESS
EXTERNALLY_RESOLVED
VALIDATION_PENDING
RESOLVED
CLOSED
CANCELLED
~~~

Treatment strategy:

~~~text
FIX
EXTERNAL_NONCOMPLIANCE
RISK_EXCEPTION
MIXED
~~~

## REMEDIATION_CASE_FINDING

~~~text
remediation_case_finding_id
remediation_case_id
finding_id
relationship_type
added_at
added_by
removed_at
removed_by
removal_reason
~~~

Active-row constraint:

~~~text
At most one active link for
(remediation_case_id, finding_id, relationship_type)
~~~

## ISSUE_REFERENCE

Reference to the authoritative record in an external issue-management system.

~~~text
issue_reference_id
remediation_case_id
connector_name
external_issue_id
external_issue_key
external_url
synchronization_status
last_observed_status
last_observed_payload_json
last_synchronized_at
creation_idempotency_key
created_at
updated_at
revision
~~~

Constraints:

~~~text
UNIQUE(connector_name, external_issue_id)
UNIQUE(creation_idempotency_key)
~~~

ISRP stores a synchronized summary and link. The external issue-management platform remains authoritative for the issue's native lifecycle. Outbound creation uses the transactional outbox; inbound status updates use INTEGRATION_INBOX_EVENT.

## CORRECTIVE_ACTION_PLAN

A remediation case may have multiple plan versions, but at most one active approved plan.

~~~text
cap_id
remediation_case_id
plan_version
status
summary
owner_org_id
owner_actor_id
proposed_at
approved_at
approved_by
target_completion_date
completed_at
supersedes_cap_id
revision
~~~

Status:

~~~text
DRAFT
SUBMITTED
APPROVED
IN_PROGRESS
COMPLETED
SUPERSEDED
CANCELLED
~~~

Constraints:

~~~text
UNIQUE(remediation_case_id, plan_version)
At most one active APPROVED or IN_PROGRESS plan per remediation case
~~~

## CAP_ACTION_ITEM

~~~text
cap_action_item_id
cap_id
action_number
title
description
status
owner_org_id
owner_actor_id
target_date
completed_at
completion_summary
revision
~~~

Status:

~~~text
NOT_STARTED
IN_PROGRESS
BLOCKED
COMPLETED
CANCELLED
~~~

## CAP_ACTION_FINDING

~~~text
cap_action_finding_id
cap_action_item_id
finding_id
relationship_type
~~~

Constraint:

~~~text
UNIQUE(cap_action_item_id, finding_id)
~~~

## CAP_ACTION_REQUIREMENT

~~~text
cap_action_requirement_id
cap_action_item_id
assessment_requirement_id
relationship_type
~~~

Constraint:

~~~text
UNIQUE(cap_action_item_id, assessment_requirement_id)
~~~

## CAP_EVIDENCE

~~~text
cap_evidence_id
cap_id
cap_action_item_id
evidence_version_id
relationship_type
created_at
created_by
~~~

CAP evidence references immutable EVIDENCE_VERSION records. cap_action_item_id may be null when evidence applies to the whole plan.

## RISK_EXCEPTION

~~~text
exception_id
assessment_id
assessment_requirement_id
finding_id
remediation_case_id
exception_type
status
rationale
compensating_controls
risk_owner_id
approved_by
approved_at
valid_from
expires_at
supersedes_exception_id
~~~

An exception does not change historical reviewer determinations. Policy determines its effect on final decision, finding treatment, remediation, and closure.

# Part XI: Deterministic metadata proposals

## METADATA_CHANGE_PROPOSAL

Current source-neutral proposal used for rule or integration results that require human confirmation.

~~~text
proposal_id
target_type
target_id
field_name
current_value_json
proposed_value_json
source_type
source_reference
rule_id
rule_version
confidence
rationale
status
created_at
created_by
applied_at
applied_by
~~~

Current source types:

~~~text
RULE
INTEGRATION
~~~

Future reserved:

~~~text
AI
~~~

Status:

~~~text
PROPOSED
APPROVED
APPROVED_WITH_EDIT
REJECTED
EXPIRED
APPLIED
~~~

Current validation rejects AI as a source type.

## PROPOSAL_EVIDENCE_SOURCE

~~~text
proposal_evidence_source_id
proposal_id
evidence_version_id
page_number
section_reference
citation_blurb
~~~

Current deterministic proposals normally reference structured or explicitly selected source data rather than inferred security meaning.

## PROPOSAL_DECISION

~~~text
proposal_decision_id
proposal_id
decision
decided_by
decided_at
edited_value_json
justification
apply_command_id
~~~

# Part XII: Audit, idempotency, and integration events

## AUDIT_EVENT

Security and business action audit.

~~~text
audit_event_id
event_type
aggregate_type
aggregate_id
actor_id
actor_org_id
actor_role
occurred_at
correlation_id
command_id
workflow_instance_id
step_instance_id
previous_revision
new_revision
changed_fields_json
reason_reference
source_channel
~~~

Audit events explain who performed an action. They are not substitutes for immutable business records.

Important event types include:

~~~text
REQUEST_SUBMITTED
REQUEST_STATUS_CHANGED
ASSESSMENT_CREATED
ASSESSMENT_STATUS_CHANGED
REQUIREMENT_SELECTED
REQUIREMENT_SCOPE_CHANGED
WORK_PACKAGE_ASSIGNED
RESPONSE_SUBMITTED
CLARIFICATION_REQUESTED
COMMENT_ADDED
EVIDENCE_ATTACHED
EVIDENCE_REPLACED
CITATION_ADDED
CITATION_SUPERSEDED
RESPONDER_ASSERTION_RECORDED
REVIEWER_DETERMINATION_RECORDED
FINAL_DECISION_RECORDED
EXCEPTION_ACCEPTED
FINDING_CREATED
REMEDIATION_CASE_CREATED
NONCOMPLIANCE_REGISTRATION_REQUESTED
ISSUE_CREATED
ISSUE_STATUS_SYNCHRONIZED
CAP_APPROVED
CAP_ACTION_COMPLETED
REMEDIATION_VALIDATION_REQUESTED
FINDING_VALIDATED
~~~

## COMMAND_DEDUPLICATION

~~~text
command_id
command_type
aggregate_type
aggregate_id
received_at
completed_at
result_reference
status
~~~

Constraint:

~~~text
PRIMARY KEY(command_id)
~~~

Repeating a completed command returns or reconstructs the prior result.

## OUTBOX_EVENT

Reliable message waiting for delivery.

~~~text
event_id
event_type
aggregate_type
aggregate_id
aggregate_version
occurred_at
payload_json
correlation_id
published_at
attempt_count
next_attempt_at
claimed_by
claimed_at
last_error
~~~

Constraints:

~~~text
PRIMARY KEY(event_id)

UNIQUE(
  aggregate_type,
  aggregate_id,
  aggregate_version,
  event_type
)
~~~

Business change, immutable history, audit event, and outbox event commit in one transaction.

## INTEGRATION_INBOX_EVENT

Durable receipt and processing state for an event received from an external provider or connector.

~~~text
inbox_event_id
connector_name
provider_event_id
event_type
received_at
payload_json
correlation_id
processing_status
attempt_count
next_attempt_at
claimed_by
claimed_at
processed_at
last_error
~~~

Constraints:

~~~text
PRIMARY KEY(inbox_event_id)
UNIQUE(connector_name, provider_event_id)
~~~

The raw receipt is persisted before domain handling. Processing is idempotent. If a provider has no event ID, the connector derives a stable deduplication key from provider identifiers and payload identity. Reconciliation uses the same command path as event delivery.

## PROCESSED_EVENT

Consumer idempotency.

~~~text
consumer_name
event_id
processed_at
source_version
~~~

Constraint:

~~~text
PRIMARY KEY(consumer_name, event_id)
~~~

# Part XIII: RDBMS status projections

Lifecycle state remains authoritative on ISRP_REQUEST and ISRP_ASSESSMENT. The following tables are rebuildable query projections.

## REQUEST_STATUS_PROJECTION

~~~text
request_id
lifecycle_status
primary_phase
attention_status
attention_reason
active_attention_json

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
active_remediation_case_count
open_issue_count
overdue_cap_action_count
validation_pending_count

source_version
projected_at
~~~

## ASSESSMENT_STATUS_PROJECTION

~~~text
assessment_id
lifecycle_status
primary_phase
attention_status
attention_reason
active_attention_json

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
active_remediation_case_count
open_issue_count
overdue_cap_action_count
validation_pending_count

active_work_package_count
blocked_work_package_count
waiting_for_requestor_count
waiting_for_reviewer_count

source_version
projected_at
~~~

## ASSESSMENT_ACTIVE_PHASE

Preferred normalized representation of simultaneous DAG phases.

~~~text
assessment_id
phase_code
phase_status
step_instance_id
started_at
updated_at
source_version
~~~

Constraint:

~~~text
PRIMARY KEY(assessment_id, phase_code, step_instance_id)
~~~

## Projection processing

~~~text
Authoritative child change
  -> immutable history
  -> AUDIT_EVENT
  -> OUTBOX_EVENT
  -> commit
  -> status projector
       -> ASSESSMENT_STATUS_PROJECTION
       -> REQUEST_STATUS_PROJECTION
       -> PROCESSED_EVENT
~~~

Projection processing is idempotent and version-aware. Version gaps are delayed, replayed, or repaired through rebuild.

Closure commands never rely solely on projections. They revalidate authoritative requirements, work packages, findings, remediation cases, issue references, CAP actions, exceptions, validation state, and aggregate revisions.

# Part XIV: Derived search, reporting, document, and vector models

Derived stores may be inside the same RDBMS or external.

## Request document projection

~~~text
REQUEST_DOCUMENT_PROJECTION
---------------------------
request_id
source_version
document_json
projected_at
~~~

It may embed assessment and status summaries for UI reads but is not authoritative.

## Search projection

Search documents may combine requirement text, response text, citation blurbs, comments, and subject names. PostgreSQL full-text search, Oracle Text, or an external search engine may implement it.

## Reporting projection

Typical facts and dimensions:

~~~text
REQUEST_DAILY_FACT
ASSESSMENT_DAILY_FACT
REQUIREMENT_DECISION_FACT
DOMAIN_COMPLIANCE_SUMMARY
EVIDENCE_FRESHNESS_SUMMARY
~~~

## Vector projection

Vector retrieval is future scope when used for AI. A vector record must reference an exact evidence version and carry authorization attributes.

~~~text
EVIDENCE_EMBEDDING_PROJECTION
-----------------------------
chunk_id
evidence_version_id
assessment_id
requirement_id
chunk_text
content_hash
embedding_model
embedding_version
classification
access_scope_json
embedding
source_version
projected_at
~~~

A vector is derived content, never evidence itself.

# Part XV: Future AI extension

These entities are not part of the current deterministic implementation.

## AI_ANALYSIS_RUN

~~~text
analysis_run_id
analysis_type
model_provider
model_name
model_version
prompt_template_id
prompt_template_version
input_snapshot_hash
status
started_at
completed_at
requested_by
supervisor_id
cost_metadata_json
~~~

## AI_ANALYSIS_INPUT

~~~text
analysis_input_id
analysis_run_id
evidence_version_id
assessment_requirement_id
input_role
~~~

## AI_ANALYSIS_OUTPUT

~~~text
analysis_output_id
analysis_run_id
raw_output_reference
parsed_output_json
policy_result
confidence_summary
created_at
~~~

## REQUIREMENT_APPLICABILITY_PROPOSAL

~~~text
proposal_id
assessment_requirement_id
analysis_run_id
proposed_applicability
rationale
confidence
status
created_at
~~~

## REQUIREMENT_PREFILL_PROPOSAL

~~~text
prefill_proposal_id
assessment_requirement_id
analysis_run_id
proposed_outcome
proposed_implementation_currency
proposed_evidence_freshness
proposed_response_text
confidence
rationale
status
created_at
~~~

## PREFILL_PROPOSAL_CITATION

~~~text
prefill_proposal_citation_id
prefill_proposal_id
evidence_version_id
page_number
section_reference
citation_blurb
confidence
~~~

## AI_HUMAN_REVIEW

~~~text
ai_human_review_id
analysis_run_id
proposal_type
proposal_id
decision
reviewed_by
reviewed_at
edited_value_json
justification
apply_command_id
~~~

Future AI proposals are applied only through deterministic authorized commands. Rejected proposals remain auditable but do not affect authoritative state or status projections.

# Part XVI: Core transaction invariants

## Request or assessment metadata edit

~~~text
BEGIN
  validate expected revision
  update current aggregate
  increment revision
  insert AUDIT_EVENT
  insert OUTBOX_EVENT
COMMIT
~~~

## Response submission

~~~text
BEGIN
  validate assignment and draft revision
  insert immutable RESPONSE_SUBMISSION
  insert RESPONDER_ASSERTION
  snapshot SUBMISSION_CITATION rows
  update ASSESSMENT_REQUIREMENT current pointers/status/revision
  insert AUDIT_EVENT
  insert OUTBOX_EVENT
COMMIT
~~~

## Final decision

~~~text
BEGIN
  validate decision authority
  validate submission and evidence policy
  insert immutable REQUIREMENT_FINAL_DECISION
  update ASSESSMENT_REQUIREMENT current decision/projections/revision
  create or update FINDING when policy requires
  insert AUDIT_EVENT
  insert OUTBOX_EVENT
COMMIT
~~~

## Evidence replacement

~~~text
BEGIN
  insert immutable EVIDENCE_VERSION
  update EVIDENCE_ITEM.current_version_id
  flag current dependent citations REVALIDATION_REQUIRED
  insert AUDIT_EVENT
  insert OUTBOX_EVENT
COMMIT
~~~

Historical submissions and decisions continue referencing the prior evidence version.

## Status projection event

~~~text
BEGIN
  reject duplicate PROCESSED_EVENT
  validate source version
  update assessment projection
  update request projection
  insert PROCESSED_EVENT
COMMIT
~~~

## Finding disposition and external issue integration

~~~text
BEGIN
  lock FINDING and validate revision
  record REGISTER_NONCOMPLIANCE disposition and justification
  create or update REMEDIATION_CASE
  link finding through REMEDIATION_CASE_FINDING
  insert NONCOMPLIANCE_REGISTRATION_REQUESTED OUTBOX_EVENT
  insert AUDIT_EVENT
COMMIT

Outbound connector:
  consume outbox event idempotently
  use creation_idempotency_key
  create or locate external issue
  record ISSUE_REFERENCE against REMEDIATION_CASE
  emit ISSUE_CREATED or ISSUE_SYNCHRONIZATION_FAILED
~~~

Inbound update:

~~~text
Connector webhook or reconciliation poll:
  persist INTEGRATION_INBOX_EVENT using connector_name + provider_event_id
  correlate ISSUE_REFERENCE
  update synchronized issue summary and observed status
  if external status is resolved:
      mark remediation case EXTERNALLY_RESOLVED or VALIDATION_PENDING
      create deterministic ISRP validation work
      do not close finding
  insert AUDIT_EVENT and OUTBOX_EVENT
  mark inbox event processed
~~~

The external issue system remains authoritative for external issue and CAP execution states. ISRP is authoritative for finding disposition, requirement validation, and review closure.

# Part XVII: Deletion, retention, and immutability

Normally immutable:

- published requirement versions
- published requirement-set versions
- evidence versions
- submitted responses
- responder assertions
- reviewer determinations
- final decisions
- comments
- lifecycle transition history
- audit events

Mutable current records use optimistic locking.

Logical removal uses status, removed_at, removed_by, and reason. Physical deletion follows approved retention, privacy, legal-hold, and evidence policies.

Redaction is a privileged overlay. It must not silently rewrite audit or historical meaning.

# Part XVIII: Minimum current implementation

The initial deterministic release requires:

~~~text
ACTOR_REFERENCE
ORGANIZATION_REFERENCE
SUBJECT_REFERENCE

ISRP_REQUEST
REQUEST_SUBJECT
REQUEST_STATUS_HISTORY
REQUEST_DUPLICATE_MATCH

ISRP_ASSESSMENT
ASSESSMENT_SUBJECT
ASSESSMENT_RELATIONSHIP
ASSESSMENT_STATUS_HISTORY

SECURITY_DOMAIN
IS_REQUIREMENT
IS_REQUIREMENT_VERSION
REQUIREMENT_SET
REQUIREMENT_SET_VERSION
REQUIREMENT_SET_MEMBER
ASSESSMENT_REQUIREMENT
ASSESSMENT_REQUIREMENT_CHANGE

REQUIREMENT_WORK_PACKAGE
WORK_PACKAGE_REQUIREMENT
WORK_PACKAGE_ASSIGNMENT

EVIDENCE_ITEM
EVIDENCE_VERSION
REQUEST_EVIDENCE
ASSESSMENT_EVIDENCE
SUBJECT_EVIDENCE
EVIDENCE_COLLECTION
EVIDENCE_COLLECTION_ITEM
EVIDENCE_CITATION
EVIDENCE_PROCESSING_RUN

REQUIREMENT_RESPONSE_DRAFT
REQUIREMENT_RESPONSE_SUBMISSION
SUBMISSION_CITATION
RESPONDER_ASSERTION
REVIEWER_DETERMINATION
REQUIREMENT_FINAL_DECISION
REQUIREMENT_COMMENT

FINDING
FINDING_REQUIREMENT
FINDING_SUBJECT
REMEDIATION_CASE
REMEDIATION_CASE_FINDING
ISSUE_REFERENCE
CORRECTIVE_ACTION_PLAN
CAP_ACTION_ITEM
CAP_ACTION_FINDING
CAP_ACTION_REQUIREMENT
CAP_EVIDENCE
RISK_EXCEPTION

METADATA_CHANGE_PROPOSAL
PROPOSAL_EVIDENCE_SOURCE
PROPOSAL_DECISION

COMMAND_DEDUPLICATION
AUDIT_EVENT
OUTBOX_EVENT
INTEGRATION_INBOX_EVENT
PROCESSED_EVENT

REQUEST_STATUS_PROJECTION
ASSESSMENT_STATUS_PROJECTION
ASSESSMENT_ACTIVE_PHASE
~~~

Flow definition and runtime entities remain owned by the separate generic Flow service.
