# ISRP Delivery Plan

## Delivery principles

- Deliver vertical slices with demonstrable business behavior.
- Keep ISRP domain logic out of the generic workflow service.
- Start with PostgreSQL; validate Oracle portability continuously and implement the Oracle adapter when required.
- Treat workflow definitions and requirement versions as immutable after publication.
- Make every command idempotent and every mutable aggregate revision-controlled.
- Add NoSQL or search only for a measured read, search, reporting, or AI retrieval need.

## Iteration 0: Decisions and contracts

Deliver:

- Architecture decision records for service boundaries, PostgreSQL-first storage, Oracle portability, and projection strategy
- ISRP-to-workflow command and event contract
- Status, actor-mode, and identifier vocabularies
- Initial authorization and organization model
- Nonfunctional targets for availability, latency, retention, and audit

Acceptance:

- ISRP can create and correlate a workflow without workflow-core importing ISRP code.
- Ownership of every authoritative data element is documented.

## Iteration 1: Persistence foundation

Deliver:

- PostgreSQL migrations
- Application-generated identifiers
- UTC timestamp conventions
- Revision-based optimistic locking
- Audit-event and outbox tables
- Repository interfaces and PostgreSQL adapters
- Transaction and idempotency test harness

Acceptance:

- A business update, audit event, and outbox event commit atomically.
- Concurrent edits produce a detectable revision conflict.
- Repeating an idempotent command does not duplicate data.

## Iteration 2: Request intake

Deliver:

- Request create, edit, submit, hold, cancel, and view APIs
- Trigger types: NEW, MATERIAL_CHANGE, PERIODIC
- Subject references and request scope
- Request lifecycle FSM
- Current metadata with audit history

Acceptance:

- Applications can exist without technologies.
- Technologies can exist without applications.
- A request can include external and on-premises subjects.
- Editing current metadata preserves an audit record.

## Iteration 3: Duplicate and overlap detection

Deliver:

- Exact source identifier matching
- Idempotent submission
- Scope fingerprint
- Active-request and active-assessment candidate search
- Overlap warning with rationale
- Link, cancel, or documented-override decisions

Acceptance:

- A duplicate submission cannot silently create a second request.
- Similar-but-distinct scope can proceed with a captured justification.
- The system detects an active vendor assessment when a new request includes the same external subject.

## Iteration 4: Categorization and assessment creation

Deliver:

- Categorization rules and decisions
- External, internal-application, and infrastructure assessment types
- Request-to-assessment orchestration DAG
- Assessment ownership by organization
- Assessment scope copied or narrowed from request scope
- Request roll-up projection

Acceptance:

- One request can create external and infrastructure assessments in parallel.
- An infrastructure-only subject can create an assessment without an application.
- Each assessment receives an immutable workflow-definition version.

## Iteration 5: Assessment DAG and step FSM

Deliver:

- Assessment execution DAG
- Human work items and organizational assignments
- Step assignment, work, clarification, response, submission, and completion states
- Conditional edges
- Parallel SME branches
- ALL and ANY joins
- Durable timers and escalation signals

Acceptance:

- Multiple organizations can complete SME tasks concurrently.
- Clarification can cycle multiple times inside a step.
- Join completion is deterministic and idempotent.
- A failed or blocked branch produces the configured attention status.

## Iteration 6: Requirement catalog and applicability

Deliver:

- Stable requirements and immutable requirement versions
- Domain/group classifications
- Requirement-set rules
- Assessment requirement snapshots
- Applicability and reviewer assignment
- Requirement-level access control

Acceptance:

- Editing a catalog requirement does not alter an active assessment.
- Reviewers can be assigned all or a subset of requirements.
- Applicability decisions include rationale and actor identity.

## Iteration 7: Responses and evidence

Deliver:

- Editable response drafts
- Immutable numbered submissions
- Reviewer feedback
- Clarification and resubmission
- Requirement decisions
- Evidence references and hashes

Acceptance:

- Draft edits overwrite only the current draft.
- Submitted versions cannot be edited.
- Feedback and decisions always reference the submission reviewed.
- Evidence authorization is enforced independently of assessment metadata.

## Iteration 8: Actor modes and automation

Deliver:

- HUMAN, AI_ASSISTED_HUMAN, AI_AUTOMATED_SUPERVISED, and AUTOMATION modes
- Completion authority policy
- AI provenance records
- Human supervisor approval
- Automated connector tasks
- Retry, timeout, and failure policies

Acceptance:

- AI assistance cannot complete a human-authority task.
- Supervised AI output remains pending until an authorized supervisor acts.
- Automation retries do not duplicate side effects.

## Iteration 9: Parent-child projections

Deliver:

- Request and assessment lifecycle, phase, attention, and progress projections
- Priority-ordered roll-up rules
- Projection version and timestamp
- Administrative projection rebuild
- UI summaries and drill-down

Acceptance:

- A child clarification changes attention without incorrectly resetting the parent lifecycle.
- All completed required assessments make a request READY_TO_CLOSE.
- Rebuilding projections produces the same result as incremental processing.

## Iteration 10: Findings and issue integration

Deliver:

- Findings
- Noncompliance classification
- Issue eligibility and closure policies
- Issue-management outbox connector
- External issue references and status synchronization
- Reconciliation operation

Acceptance:

- Retrying issue creation produces one external issue.
- An assessment can close with an open issue only when policy permits.
- External status changes are traceable and reconcilable.

## Iteration 11: Enterprise controls

Deliver:

- RBAC plus organization and attribute-based checks
- Separation of duties
- SLA timers and escalations
- Data classification
- Retention and archival
- Audit export
- Operational dashboards
- Definition publication and approval governance

Acceptance:

- Cross-organization access follows explicit policy.
- Privileged actions and definition changes are auditable.
- Retention and legal-hold rules do not destroy required evidence.

## Iteration 12: Scale and additional stores

Deliver as justified:

- Oracle persistence adapter and dialect migrations
- NoSQL request and assessment projections
- Search index
- Reporting warehouse feed
- AI/vector retrieval projection
- Partitioning, archival, performance, failover, and recovery tests

Acceptance:

- Authoritative writes still complete in one RDBMS transaction.
- Every projection can be rebuilt from authoritative data and events.
- PostgreSQL and Oracle adapters pass the same repository contract tests.

## Recommended release grouping

Release 1 — Intake foundation:

- Iterations 0 through 4
- Creates requests, detects duplicates, categorizes scope, and starts assessments

Release 2 — Assessment execution:

- Iterations 5 through 7
- Runs parallel reviews and manages versioned requirement responses

Release 3 — Automation and governance:

- Iterations 8 through 11
- Adds actor modes, roll-ups, issue integration, and enterprise controls

Release 4 — Scale options:

- Iteration 12
- Adds only the storage and performance capabilities justified by production evidence
