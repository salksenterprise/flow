# Information Security Review Process

This section defines the Information Security Review Process (ISRP), including
the orchestration it performs. State machines, dependency graphs, durable
timers, assignments, jobs, signals, and reliability plumbing are ISRP code in
this repository, expressed with ISRP types. There is no separate workflow
engine, embedded product, or workflow service.

An earlier design separated a domain-neutral workflow component from ISRP,
first as a shared service and later as an embedded library. Both designs were
abandoned. The component had one consumer, duplicated concepts already present
in ISRP, and created a boundary that made the model and its documentation drift.
[Orchestration](orchestration.md) records the decision and the retained
load-bearing execution behavior.

## Document map

- [Orchestration](orchestration.md): authoritative execution design, including
  state dimensions, graphs, timers, jobs, signals, reliability, and migration.
- [Architecture](architecture.md): application boundaries, lifecycle hierarchy,
  actor modes, transaction ownership, and status aggregation.
- [Canonical data model](data-model.md): authoritative entities, relationships,
  fields, constraints, history, evidence, requirements, and execution records.
- [Workflow visual guide](workflow-visual-guide.md): ASCII walkthrough of
  requests, assessments, steps, findings, remediation, validation, and roll-ups.
- [IS requirements](requirements-catalog.md): catalog selection, work packages,
  assertions, evidence, determinations, decisions, and orchestration integration.
- [Status projections](status-projections.md): authoritative lifecycle state,
  derived summaries, event processing, parallel phases, and closure validation.
- [Current and future phases](phases.md): deterministic current scope and
  separately governed future AI-assisted capabilities.
- [Delivery plan](delivery-plan.md): implementation sequence and acceptance
  criteria for the fused ISRP application.

## Core decisions

1. One ISRP request owns one or more assessments.
2. An assessment belongs to one request unless a future reuse requirement is
   explicitly approved.
3. A request uses an FSM for lifecycle and a DAG to orchestrate assessments.
4. An assessment uses an FSM for lifecycle and a DAG for phases, conditional
   paths, parallel reviews, and joins.
5. Each executable step uses an FSM for assignment, work, clarification,
   submission, review, and completion.
6. ISRP owns requests, assessments, requirements, responses, evidence,
   findings, remediation cases, CAPs, issue references, definitions, runtime
   steps, assignments, timers, jobs, signals, events, inbox, and outbox.
7. A request or assessment is the orchestration aggregate; there is no separate
   `workflow_instance` record.
8. Request and assessment rows carry `lifecycle_status`, `execution_status`,
   `revision`, and their pinned `workflow_version_id` directly.
9. Oracle is the intended production store. SQLite is the implemented
   development and test adapter. Oracle support cannot be claimed until its
   adapter passes the shared repository contract suite against a real instance.
10. The repository interface exists only for SQLite/Oracle portability. It is
    not a reusable-engine boundary.
11. Current metadata may be updated with optimistic locking, but each accepted
    change emits an ordered audit event.
12. Requirement drafts are editable; submitted responses are immutable and
    versioned.
13. Compliance outcome, implementation currency, and evidence freshness are
    separate dimensions.
14. Responder assertions, reviewer determinations, and final decisions are
    independent immutable records with distinct authority.
15. Request and assessment lifecycle states are authoritative. Attention,
    phase, progress, and compliance summaries are rebuildable projections.
16. The current release supports only HUMAN and AUTOMATION execution. AI modes
    require separately published future definitions and mandatory governance.
17. A finding belongs to the assessment that discovered it and links to the
    affected requirements and subjects.
18. A remediation case groups findings; issue references and corrective action
    plans belong to that remediation case.
19. Request-level finding and remediation status is derived through its
    assessments, not copied into request-owned records.
20. External issue creation uses the ISRP outbox. Inbound provider updates use
    the ISRP inbox with idempotent correlation.
21. An external issue reported resolved creates validation work; it never
    closes an ISRP finding automatically.
22. Closure commands validate authoritative findings, remediation cases, CAP
    actions, issue references, and exceptions according to policy.
23. The ISRP application owns authentication, authorization, transactions,
    migrations, scheduling, and every orchestration invocation.
24. Domain changes, orchestration changes, ordered events, and outbox records
    required by one business command commit or roll back together.
25. ISRP has one ordered event log, one outbox, and one inbox. Execution events
    use `ISRP_REQUEST` or `ISRP_ASSESSMENT`; no `WORKFLOW` aggregate exists.
