# Information Security Review Process

This section defines the Information Security Review Process (ISRP), including
the orchestration it performs. ISRP owns its state machines, dependency graphs,
durable timers and reliability plumbing as its own code. There is no separate
workflow engine, service or package.

An earlier revision separated a domain-neutral workflow core from ISRP. That
core had exactly one consumer, and maintaining the boundary between the two
produced most of the defects found in review. See
[Orchestration](orchestration.md) for what the fusion removes and what it
keeps.

Documents:

- [Orchestration](orchestration.md): how ISRP runs its own state machines,
  dependency graphs, timers and reliability plumbing. ISRP owns this; there is
  no separate workflow engine.

- [Architecture](architecture.md): embedded module boundaries, lifecycle model, DAG/FSM responsibilities, actor modes, and parent-child status aggregation.
- [Workflow visual guide](workflow-visual-guide.md): ASCII walkthrough of request, assessment, and step workflows, plus gap disposition, remediation cases, external issues, CAPs, validation, and status roll-ups.
- [Canonical data model](data-model.md): the consolidated source of truth for all ISRP and Flow-related entities, relationships, constraints, history, evidence, requirements, projections, events, portability, and future extensions.
- [Flow to IS Requirements](requirements-catalog.md): catalog structure, requirement selection, work packages, actor assertions, evidence, decisions, audit, and workflow integration.
- [RDBMS status projections](status-projections.md): authoritative lifecycle states, derived status summaries, outbox processing, parallel phases, and closure validation.
- [Current and future phases](phases.md): deterministic current scope and separately governed future AI-assisted capabilities.
- [Delivery plan](delivery-plan.md): phased implementation plan, acceptance criteria, and sequencing.

## Core decisions

1. One ISRP request owns one or more ISRP assessments.
2. An assessment belongs to one request unless a future, explicit reuse requirement is approved.
3. A request uses an FSM for its lifecycle and a DAG to orchestrate assessments.
4. An assessment uses an FSM for its lifecycle and a DAG for phases, conditional paths, parallel reviews, and joins.
5. Each executable step uses an FSM for assignment, work, clarification, submission, and completion.
6. The ISRP host application owns requests, assessments, requirements, responses, evidence references, findings, remediation cases, CAPs, and external issue references.
7. The embedded Flow core logically owns definitions, workflow instances, step instances, transitions, work items, timers, execution audit events, and execution outbox events. Its tables live in the ISRP database under an owned schema or table prefix.
8. Oracle is the intended production authoritative store. SQLite is the locally verified development and test adapter. Oracle behavior remains unverified until an Oracle environment can run the shared repository contract suite; PostgreSQL is a later optional adapter. NoSQL is an optional read/search projection, not the system of record.
9. Current metadata may be overwritten with optimistic locking, but every accepted change produces an audit event.
10. Requirement drafts are editable; submitted requirement responses are immutable and versioned.
11. Compliance outcome, implementation currency, and evidence freshness are modeled separately.
12. Responder assertions, reviewer determinations, and final decisions are independent, immutable records with distinct authorities.
13. Request and assessment lifecycle states are authoritative; attention, phase, progress, and compliance summaries are rebuildable RDBMS projections maintained from outbox events.
14. The current ISRP release is deterministic and supports only HUMAN and AUTOMATION execution. All AI capabilities are future-phase features delivered through new workflow versions and mandatory human governance.
15. A finding is owned by the assessment that discovered it and links to one or more affected assessment requirements and subjects.
16. A remediation case groups one or more findings; issue-management references and corrective action plans belong to the remediation case.
17. Request-level finding, remediation, issue, and CAP status is derived through the request's assessments rather than duplicated as request-owned records.
18. External issue creation uses the shared outbox; inbound issue updates use the shared inbox with idempotent correlation.
19. An external issue reported as resolved creates validation work. It does not automatically close the ISRP finding.
20. Closure commands validate authoritative findings, remediation cases, CAP actions, issue references, and exceptions according to policy.
21. The ISRP application owns the connection, transaction, authentication, authorization context, migration invocation, and scheduling of Flow background routines.
22. ISRP and Flow writes required by one business command commit or roll back together. Stable ISRP-to-Flow relationships may use database-enforced foreign keys.
23. ISRP and Flow share one event log, one outbox and one inbox. ISRP writes them through the engine, under its own aggregate types; `WORKFLOW` is reserved for Flow. ISRP defines no parallel audit, outbox or inbox table.
