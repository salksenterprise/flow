# Flow to IS Requirements

> Canonical model: All entity definitions, fields, relationships, constraints, and ownership decisions are consolidated in [Data model](data-model.md). This document describes behavior and uses abbreviated entity views only.

## Purpose

This document explains how the embedded generic Flow workflow core and the ISRP
requirements domain work together inside one host application. Flow controls
orchestration and work progression. ISRP controls the meaning, selection,
response, evidence, review, and decision history of security requirements.

## Responsibility boundary

| Concern | Flow | ISRP |
|---|---|---|
| Workflow definitions and versions | Owns | References |
| DAG nodes, edges, forks, and joins | Owns | Configures through published templates |
| Step FSM and work-item execution | Owns | Correlates to domain work |
| Current human and deterministic automation execution | Owns | Defines business authority |
| Future AI-assisted execution | Reserved for future workflow versions | Owns proposal approval and authoritative decisions |
| Request and assessment metadata | References by opaque key | Owns |
| Requirement catalog and requirement sets | Does not interpret | Owns |
| Assessment requirement snapshot | Does not interpret | Owns |
| Assertions, determinations, and decisions | Receives progress events | Owns |
| Evidence, citations, comments, and findings | Does not store domain records | Owns |
| Assignment mechanics | Owns work item | Owns requirement scope and authority |
| Audit | Execution audit | Business and compliance audit |


## Current-phase execution boundary

The current ISRP implementation is deterministic. Requirement-set selection uses published rules and structured intake data. Humans create responder assertions, evidence citations, reviewer determinations, and final decisions. Automation validates, routes, calculates, projects, retries, and integrates but does not infer compliance from document content.

Future AI may propose metadata, applicability, response drafts, citations, missing evidence, or potential findings. These proposals remain non-authoritative until an authorized human disposition and deterministic apply command. Current and future assessments use separate published workflow versions. See [Current and future phases](phases.md).

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

Flow stores opaque ISRP business keys and never interprets the referenced
domain records. Because Flow is embedded in the ISRP database, ISRP binding
columns may use foreign keys to Flow workflow and step instances. Directional
ownership remains clear: ISRP may reference Flow execution identities; Flow
does not acquire dependencies on ISRP tables or concepts.

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

## Orchestration commands

ISRP calls the orchestration directly. There is no work-item concept, and no
workflow instance: a run belongs to a request or to an assessment, named by the
pair `(owner_type, owner_id)`.

~~~text
engine.start_request(command)              open a request on a published version
engine.start_assessment(request_id, cmd)   open an assessment under a request
engine.apply_action(step_id, command)      claim, start, complete, fail a node
                                           and request_clarification, respond,
                                           resume through the step FSM
engine.apply_lifecycle_action(owner, cmd)  move the aggregate's own lifecycle,
                                           or suspend, resume, terminate it
engine.update_facts(owner, command)        supply routing facts
engine.receive_signal(owner, command)      deliver an external occurrence
engine.record_event(...)                   record an ISRP domain event, on the
                                           same stream as the execution events
~~~

`request_clarification`, `respond` and `resume` are FSM actions on a node, not
separate commands. Which are permitted is decided by the step FSM the assessment
template pins.

An ASSESSMENT node opens its assessment itself, from the version named in its
configuration, so the common case needs no `start_assessment` call at all.

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

Parallel SMEs receive separate Flow nodes and ISRP work packages. Two of the
four policies below are Flow join rules; the other two are ISRP patterns built
from Flow primitives, and naming them alongside the join rules previously
implied Flow provided them.

Flow join rules, set on a JOIN node:

- `ALL`: every applicable predecessor is satisfied.
- `ANY`: at least one applicable predecessor is satisfied.
- `N_OF_M`: at least `required_count` predecessors are satisfied, which covers a
  fixed quorum.

ISRP patterns:

- Quorum that depends on business rules rather than a count: ISRP calculates it
  and emits a qualifying event, consumed by a `WAIT_SIGNAL` node.
- Decision authority: progression waits for a final decision regardless of SME
  count, which is again a `WAIT_SIGNAL` node fed by ISRP.

Flow also offers `ALL_REQUIRED`, which waits for every predecessor whether or
not its edge condition selected it.

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

A child requirement change updates assessment projections. Assessment changes
update request projections. Parent states summarize inner execution but do not
mirror every child state. The production design stores these projections in
the same Oracle database as the ISRP and embedded Flow records; local execution
uses SQLite. OUTBOX_EVENT records drive deterministic projection updates.
Controlled closure operations always revalidate authoritative child records.
See [RDBMS status projections](status-projections.md).

## From requirement gap to remediation

A NOT_MET or PARTIALLY_MET decision can lead to a finding, but the decision and finding are separate records. The final decision preserves the compliance conclusion; the finding manages treatment.

~~~text
ASSESSMENT_REQUIREMENT
  -> REQUIREMENT_FINAL_DECISION
       -> FINDING_REQUIREMENT
            -> FINDING
                 -> fix during assessment
                 or
                 -> REMEDIATION_CASE
                      -> ISSUE_REFERENCE
                      -> CORRECTIVE_ACTION_PLAN
~~~

A finding can cover several related requirements, and a requirement can participate in more than one distinct finding when policy allows. FINDING_SUBJECT identifies which scoped application, technology, vendor, or product is affected.

Actors record an explicit disposition and append-only justification:

~~~text
FIX_IN_ASSESSMENT
REGISTER_NONCOMPLIANCE
RISK_EXCEPTION
NOT_A_FINDING
~~~

For FIX_IN_ASSESSMENT, new response, evidence, determination, and final-decision versions are created as needed; submitted history is not overwritten. For REGISTER_NONCOMPLIANCE, ISRP creates or links a remediation case and asynchronously requests an issue in the external system.

External issue resolution is evidence of progress, not an ISRP compliance decision. It creates validation work for an authorized reviewer. Only that validation can support new requirement decisions and finding resolution.

## Transaction and audit invariants

- Outcome changes and their justification commit atomically.
- Submission and its evidence/citation snapshot commit atomically.
- Current pointers and immutable history records update in one transaction.
- Business changes and outbox events commit in one transaction.
- External effects use idempotency keys.
- Submitted records, evidence versions, citations, comments, determinations, and decisions are not edited in place.
- The business history remains authoritative even if audit events are exported elsewhere.

Important audit events include requirement selection, scope amendment, assignment, draft update, submission, clarification, comment, evidence attachment or replacement, citation supersession, assertion, determination, final decision, exception, finding creation and disposition, remediation-case linkage, CAP approval and action completion, issue registration and synchronization, and ISRP validation.

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
