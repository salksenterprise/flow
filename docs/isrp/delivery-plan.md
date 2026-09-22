# ISRP Delivery Plan

## Delivery principles

- Deliver vertical slices with demonstrable business behavior.
- Keep ISRP domain logic out of the embedded generic Flow core.
- Embed Flow in the ISRP application so domain and execution writes share one host-owned transaction.
- Target Oracle for production. Use SQLite for local development and automated contract tests; do not claim Oracle support until an Oracle environment runs the shared adapter and concurrency suites.
- Consider PostgreSQL only after Oracle and only for an approved need.
- Treat workflow definitions and requirement versions as immutable after publication.
- Make every command idempotent and every mutable aggregate revision-controlled.
- Add NoSQL or search only for a measured read, search, or reporting need in the current phase; AI retrieval remains future scope.
- Keep the current ISRP release deterministic and non-AI.

## Iteration 0: Decisions and contracts

Deliver:

- Architecture decision records for embedded module boundaries, host-owned transactions, Oracle targeting, local-verification limits, and projection strategy
- ISRP-to-Flow in-process command and event contract
- Status, actor-mode, and identifier vocabularies
- Initial authorization and organization model
- Nonfunctional targets for availability, latency, retention, and audit

Acceptance:

- ISRP can create and correlate a workflow in the same transaction without workflow-core importing ISRP code.
- Ownership of every authoritative data element is documented.
- The documentation distinguishes locally verified SQLite behavior from planned Oracle behavior.

## Iteration 1: Persistence foundation

Deliver:

- Database-neutral migration ordering and ownership contract
- SQLite reference migrations for local development and automated tests
- Oracle migration design, with execution deferred until an Oracle environment is available
- Application-generated identifiers
- UTC timestamp conventions
- Revision-based optimistic locking
- Flow's migration invoked from the ISRP migration process, which creates the
  shared event log, outbox and inbox; ISRP builds none of its own
- ISRP_COMMAND and PROCESSED_EVENT, the two reliability tables ISRP does own
- Repository interfaces and SQLite reference adapters
- Transaction and idempotency test harness

Acceptance:

- A business update, its shared-log event and its outbox row commit atomically
  in one host-owned transaction, alongside any Flow execution write.
- Concurrent edits produce a detectable revision conflict.
- Repeating an idempotent command does not duplicate data.
- No acceptance result from SQLite is represented as Oracle certification.

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
- ALL, ANY, N_OF_M and ALL_REQUIRED joins, as Flow provides them
- Quorum and decision-authority progression as WAIT_SIGNAL nodes fed by ISRP
- Durable timers, business calendars, due times and breach escalation

Acceptance:

- Multiple organizations can complete SME tasks concurrently.
- Clarification can cycle multiple times inside a step.
- Join completion is deterministic and idempotent.
- A failed or blocked branch produces the configured attention status.

## Iteration 6: Requirement catalog and applicability

Deliver:

- Security domains, stable requirements, and immutable requirement versions
- Versioned requirement sets and applicability rules
- Baseline, flow, subject, classification, and approved manual overlays
- Assessment requirement snapshots
- Requirement work packages for responder, reviewer, SME, and approver roles
- Applicability and requirement-level access control

Acceptance:

- Editing a catalog requirement does not alter an active assessment.
- Every selected requirement records its exact version and selection source.
- Different organizations can be assigned all or a subset of requirements through work packages.
- Applicability and post-launch scope changes include rationale and actor identity.

## Iteration 7: Responses and evidence

Deliver:

- Editable response drafts and immutable numbered submissions
- Separate compliance outcome, implementation currency, and evidence freshness
- Responder assertions, reviewer determinations, and final decisions
- Append-only feedback, commentary, and mandatory change justifications
- Clarification and resubmission
- Immutable evidence versions, citations, hashes, and citation revalidation

Acceptance:

- Draft edits overwrite only the current draft.
- Submitted versions, assertions, determinations, decisions, comments, evidence versions, and citations are immutable.
- Responders cannot alter reviewer determinations or final decisions.
- Reviewers cannot overwrite responder assertions.
- Outcome changes require append-only justification.
- Evidence replacement preserves the original and flags affected citations for revalidation.
- Evidence authorization is enforced independently of assessment metadata.

## Iteration 8: Deterministic actor modes and automation

Deliver:

- HUMAN and AUTOMATION execution modes
- Completion-authority policy
- Rule-based metadata augmentation
- Deterministic evidence validation, hashing, storage, and supported text extraction
- Automated connector tasks
- Retry, timeout, failure, and manual-review policies
- Validation that rejects AI modes in current ISRP definitions. Flow has no
  execution-mode field and its publication validation cannot enforce this, so
  ISRP validates its own templates before importing them. See the open
  dependencies in [Architecture](architecture.md).

Acceptance:

- Automation cannot complete a human-authority task.
- Requirement applicability and outcomes are not inferred from unstructured evidence.
- Security conclusions and citations require authorized human action.
- Automation retries do not duplicate side effects.
- Current published ISRP workflows contain no AI execution nodes.

## Iteration 9: RDBMS parent-child projections

Deliver:

- Authoritative request and assessment lifecycle FSM state
- Append-only request and assessment status-transition history
- REQUEST_STATUS_PROJECTION and ASSESSMENT_STATUS_PROJECTION in the primary RDBMS
- Normalized or JSON representation of simultaneous active phases
- Outbox-driven status projector and PROCESSED_EVENT idempotency
- Priority-ordered lifecycle, attention, progress, and compliance roll-up rules
- Projection source version, timestamp, lag monitoring, and administrative rebuild
- UI summaries and drill-down
- Authoritative closure-policy validation

Acceptance:

- A child clarification changes attention without incorrectly resetting the parent lifecycle.
- All completed required assessments make a request READY_TO_CLOSE but do not automatically close it.
- A request or assessment closure command validates authoritative children rather than trusting the projection alone.
- Duplicate outbox delivery does not double-count status totals.
- Out-of-order aggregate versions are detected and repaired or replayed.
- Parallel review branches remain visible without forcing a misleading single current phase.
- Rebuilding projections produces the same result as incremental processing.

## Iteration 10: Findings, remediation, CAP, and issue integration

Deliver:

- Assessment-owned findings linked to affected requirements and subjects
- Explicit finding disposition: fix in assessment, register noncompliance, risk exception, or not a finding
- Remediation cases that can group one or more findings
- Corrective action plans, action items, ownership, target dates, and evidence links
- Noncompliance eligibility and closure policies
- Issue-management outbox connector using NONCOMPLIANCE_REGISTRATION_REQUESTED
- External issue references owned by remediation cases
- Idempotent provider updates through the inbox shared with Flow
- Status synchronization and reconciliation
- Deterministic validation work after external resolution
- Finding, remediation, issue, CAP, and validation-pending projections

Acceptance:

- A finding remains owned by the assessment that discovered it and can link to multiple requirements and subjects.
- Several findings can share one remediation case without losing traceability.
- Retrying issue creation produces one external issue.
- Replaying one inbound provider event does not duplicate state changes or validation work.
- An externally resolved issue moves remediation to validation pending and does not automatically close a finding.
- An assessment can close with an open issue, remediation case, or CAP action only when explicit policy permits.
- External status changes are traceable and reconcilable.
- Request-level counts derive through assessments instead of duplicated request-owned findings.

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

## Iteration 12: Production database certification and scale options

Required for production:

- Oracle persistence adapter and dialect migrations
- Oracle repository-contract, concurrency, migration, failover, and recovery tests in an externally provided Oracle environment

Deliver only as justified by measured need:

- PostgreSQL adapter only if separately approved after Oracle
- NoSQL request and assessment projections
- Search index
- Reporting warehouse feed
- AI/vector retrieval projection
- Partitioning, archival, performance, failover, and recovery tests

Acceptance:

- Authoritative writes still complete in one RDBMS transaction.
- Every projection can be rebuilt from authoritative data and events.
- Oracle passes the same repository contract suite as SQLite before production use.
- PostgreSQL, if built, passes the same suite without changing core semantics.


## Future phase: AI-assisted ISRP

This phase begins only after the deterministic releases are operationally proven and AI governance is approved.

Potential deliverables:

- AI_ASSISTED_HUMAN and AI_AUTOMATED_SUPERVISED execution modes
- Evidence-content analysis
- Metadata-inference proposals
- Requirement-applicability proposals
- Response-prefill proposals
- Evidence-citation proposals
- Missing-evidence and potential-finding proposals
- Model, prompt, input, output, policy, confidence, and cost provenance
- Mandatory human disposition and deterministic application of accepted proposals
- AI evaluation, monitoring, rollback, and retirement controls
- Separately published AI-assisted workflow versions

Acceptance:

- AI output is never an authoritative responder assertion, reviewer determination, or final decision.
- Every accepted proposal records the human actor, edits, justification, and source evidence versions.
- Rejected proposals remain auditable without changing authoritative business data.
- Existing deterministic workflow instances continue on their original versions.
- AI capabilities can be disabled without preventing deterministic ISRP execution.

## Recommended release grouping

Release 1 — Intake foundation:

- Iterations 0 through 4
- Creates requests, detects duplicates, categorizes scope, and starts assessments

Release 2 — Assessment execution:

- Iterations 5 through 7
- Runs parallel reviews and manages versioned requirement responses

Release 3 — Deterministic automation and governance:

- Iterations 8 through 11
- Adds deterministic automation, roll-ups, issue integration, and enterprise controls

Release 4 — Oracle certification and scale options:

- Iteration 12
- Adds only the storage and performance capabilities justified by production evidence

Future release — AI-assisted ISRP:

- Begins after deterministic releases and AI governance approval
- Uses separate workflow-definition versions
- Adds proposal-only AI analysis with mandatory human control
