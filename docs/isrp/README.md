# Information Security Review Process

This section defines how the Information Security Review Process (ISRP) uses the generic Flow workflow service.

Documents:

- [Architecture](architecture.md): service boundaries, lifecycle model, DAG/FSM responsibilities, actor modes, and parent-child status aggregation.
- [Data model](data-model.md): logical entities, relational design, document projections, portability, audit, and consistency rules.
- [Delivery plan](delivery-plan.md): phased implementation plan, acceptance criteria, and sequencing.

## Core decisions

1. One ISRP request owns one or more ISRP assessments.
2. An assessment belongs to one request unless a future, explicit reuse requirement is approved.
3. A request uses an FSM for its lifecycle and a DAG to orchestrate assessments.
4. An assessment uses an FSM for its lifecycle and a DAG for phases, conditional paths, parallel reviews, and joins.
5. Each executable step uses an FSM for assignment, work, clarification, submission, and completion.
6. The ISRP service owns requests, assessments, requirements, responses, evidence references, findings, and issue links.
7. The generic workflow service owns definitions, workflow instances, step instances, transitions, work items, timers, audit events, and delivery events.
8. PostgreSQL is the recommended initial authoritative data store. Oracle is supported through a separate persistence adapter. NoSQL is an optional read/search projection, not the system of record.
9. Current metadata may be overwritten with optimistic locking, but every accepted change produces an audit event.
10. Requirement drafts are editable; submitted requirement responses are immutable and versioned.
