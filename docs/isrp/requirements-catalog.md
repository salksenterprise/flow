# Flow to IS Requirements

## Purpose

This document explains how the generic Flow workflow service and the ISRP requirements domain work together. Flow controls orchestration and work progression. ISRP controls the meaning, selection, response, evidence, review, and decision history of security requirements.

## Responsibility boundary

| Concern | Flow | ISRP |
|---|---|---|
| Workflow definitions and versions | Owns | References |
| DAG nodes, edges, forks, and joins | Owns | Configures through published templates |
| Step FSM and work-item execution | Owns | Correlates to domain work |
| Human, AI, and automation execution modes | Owns | Defines business authority |
| Request and assessment metadata | References by opaque key | Owns |
| Requirement catalog and requirement sets | Does not interpret | Owns |
| Assessment requirement snapshot | Does not interpret | Owns |
| Assertions, determinations, and decisions | Receives progress events | Owns |
| Evidence, citations, comments, and findings | Does not store domain records | Owns |
| Assignment mechanics | Owns work item | Owns requirement scope and authority |
| Audit | Execution audit | Business and compliance audit |

## End-to-end structure

~~~text
ISRP Request
  -> one or more ISRP Assessments
       -> published Flow workflow version
       -> selected requirement-set versions
       -> assessment requirement snapshot
       -> Flow assessment DAG
            -> Flow step instance
                 -> ISRP requirement work package
                      -> selected assessment requirements
                      -> assignments and authorities
                      -> responses and evidence
                      -> determinations and decisions
            -> conditional transition or join
       -> assessment roll-up
  -> request roll-up and closure
~~~

The workflow instance stores opaque business references. It does not use foreign keys into the ISRP database.

## Catalog structure

~~~text
Security Domain
  -> IS Requirement
       -> immutable Requirement Version

Requirement Set
  -> immutable Requirement Set Version
       -> Requirement Set Members
            -> exact Requirement Versions
            -> applicability rules
            -> default actor roles
~~~

A security domain is an organizational classification such as Authentication Domain Standards. A requirement has a stable identity and code. Published requirement text, guidance, expected evidence, and response schema live in immutable versions.

Requirement sets are reusable profiles for flows such as:

- External SaaS
- External SaaS with on-premises components
- Internal application
- Infrastructure software
- Infrastructure hardware
- Security architecture review
- Threat-model assessment

## Selecting requirements for an assessment

Assessment creation resolves requirements from ordered sources:

1. Organizational baseline
2. Assessment-flow requirement set
3. Subject or technology overlay
4. Data-classification or risk overlay
5. Approved additions or removals

The result is materialized as ASSESSMENT_REQUIREMENT records. Each record retains:

- exact requirement version
- requirement-set version and selection source
- required or optional flag
- applicability state and rationale
- current response state
- current assertion, determination, and final-decision pointers
- current query projections
- optimistic revision

A catalog update never changes an active assessment. Post-launch additions or removals require authorization, rationale, and audit.

## Flow workflow and requirement work packages

A Flow node can activate one or more ISRP work packages. A package selects assessment requirements and assigns a business role:

~~~text
RESPONDER
REVIEWER
SME_REVIEWER
APPROVER
OBSERVER
~~~

Example external assessment:

~~~text
Vendor responder package
  ADS-001
  ADS-002
  ADS-004

Internal identity SME package
  ADS-001
  ADS-002

Network SME package
  NET-001
  NET-005

Security approval package
  all requirements requiring final decision
~~~

The same assessment requirement may appear in several packages. Each actor contributes a separate record; packages never cause one actor's conclusion to overwrite another's.

Flow owns assignment state, due dates, timers, delegation, retry, and escalation. ISRP owns which requirements are in the package and what authority the assignment grants.

## Requirement and package states

Requirement response lifecycle:

~~~text
NOT_STARTED
  -> DRAFT
  -> SUBMITTED
  -> IN_REVIEW
  -> CLARIFICATION_REQUIRED
  -> DRAFT
  -> RESUBMITTED
  -> DECIDED
~~~

Work-package lifecycle:

~~~text
PENDING
  -> ASSIGNED
  -> IN_PROGRESS
  -> SUBMITTED
  -> IN_REVIEW
  -> CLARIFICATION_REQUIRED
  -> RESUBMITTED
  -> COMPLETED
~~~

Comments and evidence additions do not automatically change state. State changes occur through explicit authorized commands.

## Three evaluation dimensions

Compliance outcome:

~~~text
NOT_ASSESSED
MET
PARTIALLY_MET
NOT_MET
NOT_APPLICABLE
~~~

Implementation currency:

~~~text
UNKNOWN
CURRENT
OUTDATED
PLANNED_REPLACEMENT
DECOMMISSIONED
~~~

Evidence freshness:

~~~text
NOT_PROVIDED
CURRENT
STALE
EXPIRED
NOT_REQUIRED
~~~

These dimensions are independent. For example, MET plus STALE evidence becomes the display label “Met — evidence is stale,” while preserving MET as the compliance outcome.

Stale evidence normally creates an attention condition rather than automatically changing compliance to NOT_MET. Policy may require fresh evidence before a final decision.

## Three authority layers

### Responder assertion

An immutable RESPONDER_ASSERTION records what the requestor, vendor, engineer, or control owner claims. A later assertion supersedes, but never overwrites, the earlier assertion.

### Reviewer determination

An immutable REVIEWER_DETERMINATION records an assigned reviewer or SME conclusion. Several SMEs may produce independent determinations for one requirement. A reviewer cannot change the responder assertion.

### Final decision

An immutable REQUIREMENT_FINAL_DECISION is the authoritative disposition for reporting, findings, and closure. Only a configured decision authority can create it. A later decision supersedes, but never edits, the prior decision.

Resolution order for display and roll-up:

1. Use the current final decision when present.
2. Otherwise show the applicable reviewer determination as provisional.
3. Otherwise show the responder assertion as unverified.
4. Otherwise show NOT_ASSESSED.

## Drafts and immutable submissions

The active response draft is editable by authorized assigned actors and uses optimistic locking. Submission creates:

- immutable response submission
- immutable responder assertion
- exact evidence-version links
- exact citation links
- actor, role, organization, and timestamp

Clarification creates a new draft, normally initialized from the last submission. Resubmission creates another numbered immutable submission.

## Evidence and citations

EVIDENCE_ITEM is the logical attachment. EVIDENCE_VERSION is the immutable uploaded file.

Replacement means:

1. Upload a new version.
2. Record content hash, actor, time, scan status, and replacement reason.
3. Point the logical item to the new current version.
4. Mark the prior version superseded.
5. Preserve every historical reference to the prior version.
6. Flag affected citations for revalidation.

EVIDENCE_CITATION links a requirement to an exact evidence version and stores a supporting blurb plus page, section, line, control, or diagram locator. Citation corrections create superseding citations.

## Append-only commentary and justification

REQUIREMENT_COMMENT supports general notes, clarification requests and responses, review notes, evidence notes, decision rationale, and corrections. Comments are append-only. Corrections reference the earlier comment.

Mandatory append-only justification is required for:

- outcome change
- reviewer disagreement with responder
- implementation-currency change
- stale or expired evidence classification
- evidence replacement
- not-applicable decision
- final-decision change
- reopening a decided requirement
- exception acceptance
- post-launch scope change

## Workflow events and commands

Typical ISRP commands to Flow:

~~~text
START_WORKFLOW
CREATE_WORK_ITEM
COMPLETE_WORK_ITEM
REQUEST_CLARIFICATION
RESUME_WORK_ITEM
CANCEL_WORK_ITEM
~~~

Typical ISRP domain events consumed by the integration layer:

~~~text
WORK_PACKAGE_ASSIGNED
RESPONSE_SUBMITTED
CLARIFICATION_REQUESTED
RESPONSE_RESUBMITTED
REVIEWER_DETERMINATION_RECORDED
FINAL_DECISION_RECORDED
WORK_PACKAGE_COMPLETED
FINDING_CREATED
~~~

Flow evaluates transitions using configured event names and data conditions. It treats outcome codes as opaque values supplied by ISRP.

## Parallel review and joins

Parallel SMEs receive separate Flow work items and ISRP work packages. Join policy is explicit:

- ALL: every required package must reach its configured completion state.
- ANY: one qualifying package may continue the flow.
- QUORUM: ISRP calculates the business quorum and emits a qualifying event.
- DECISION_AUTHORITY: progression waits for a final decision, regardless of SME count.

Flow should not infer agreement among conflicting SME determinations. ISRP resolves conflicts through an approval package or decision rule and then emits the authoritative completion event.

## Parent roll-ups

Assessment and request summaries expose separate values:

- lifecycle status
- current phase
- attention status
- progress counts
- compliance summary
- evidence-refresh count
- remediation count

Examples:

~~~text
MET + STALE evidence
  -> compliance MET
  -> attention EVIDENCE_REFRESH_REQUIRED

MET + OUTDATED implementation
  -> compliance MET
  -> attention IMPLEMENTATION_REVIEW_REQUIRED

NOT_MET
  -> compliance NOT_MET
  -> attention REMEDIATION_REQUIRED
~~~

A child requirement change updates assessment projections. Assessment changes update request projections. Parent states summarize inner execution but do not mirror every child state.

## Transaction and audit invariants

- Outcome changes and their justification commit atomically.
- Submission and its evidence/citation snapshot commit atomically.
- Current pointers and immutable history records update in one transaction.
- Business changes and outbox events commit in one transaction.
- External effects use idempotency keys.
- Submitted records, evidence versions, citations, comments, determinations, and decisions are not edited in place.
- The business history remains authoritative even if audit events are exported elsewhere.

Important audit events include requirement selection, scope amendment, assignment, draft update, submission, clarification, comment, evidence attachment or replacement, citation supersession, assertion, determination, final decision, exception, finding, and issue creation.

## Example: external product with infrastructure component

~~~text
Request: Adopt SecureCloud
  Subjects:
    application catalog record
    vendor/SaaS record
    on-premises connector technology

Categorization:
  create External Assessment
  create Infrastructure Assessment

External Assessment:
  resolve External SaaS requirement set
  create vendor responder package
  create internal SME packages in parallel
  collect assertions, evidence, citations, and determinations
  create final decisions
  produce findings when required

Infrastructure Assessment:
  resolve Infrastructure Software requirement set
  wait for lab-build milestone if configured
  assign infrastructure and security reviewers
  collect evidence and decisions

Request:
  remain ASSESSMENTS_IN_PROGRESS
  show aggregated attention and progress
  become READY_TO_CLOSE only when required assessments satisfy closure policy
~~~
