# Workflow Engine Documentation

This documentation defines the domain-neutral Flow workflow engine delivered through Releases 1–3.

Documents:

- [Workflow engine specification](specification.md): scope, architecture, high-level design, low-level design, runtime semantics, APIs, persistence, reliability, security, and acceptance criteria.
- [Workflow engine visual guide](visual-guide.md): ASCII walkthrough of definitions, runtime instances, FSMs, DAGs, parent-child workflows, signals, automation, timers, assignments, and reliable events.
- [Adopting Flow for another business process](adoption-guide.md): a practical method for applying Flow to processes outside review and assessment domains, including an order-fulfillment example.
- [Integration contract](../integration.md): current HTTP command, signal, automation, timer, inbox, and webhook interfaces.
- [Releases 1–3](../workflow-engine-releases-1-3.md): implemented capability summary and verification scope.

## Canonical boundaries

Flow owns execution. A client domain owns business meaning and business records.

~~~text
CLIENT DOMAIN SERVICE                  FLOW WORKFLOW SERVICE

Orders, cases, assessments             Definitions and versions
Requirements and evidence              Workflow instances
Products and customers                 DAG node instances
Business decisions                     FSM transitions
Findings and issues                    Assignments and attempts
Payments and shipments                 Signals and timers
Domain audit                           Execution audit
                                       Inbox and outbox
~~~

When a client-specific term appears in an example, it is an opaque business reference from Flow's perspective.
