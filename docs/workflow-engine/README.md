# Workflow Engine Documentation

Flow is an embeddable, domain-neutral workflow execution core. An application
adds it as a library and calls the engine inside its own database transaction;
an HTTP service shell wraps the same core for callers that cannot embed.

Start here:

- [Charter](../charter.md): the problem, the decisions taken, and the non-goals.
- [Requirements](../requirements.md): every requirement with a status and, where
  the status is `Done`, a test that cites it.
- [Technical design](../technical-design.md): how the mechanisms work and why
  they are shaped the way they are.
- [Engine reference](specification.md): entities, fields, vocabularies,
  configuration keys, endpoints and conformance criteria.
- [Integration contract](../integration.md): the HTTP surface for a client
  integrator.
- [Visual guide](visual-guide.md): ASCII walkthrough of the runtime concepts.
- [Adopting Flow for another business process](adoption-guide.md): a method for
  applying Flow outside review and assessment domains.
- [Releases 1-3](../workflow-engine-releases-1-3.md): what the earlier
  standalone-service delivery contained.

## Canonical boundaries

Flow owns execution. A client domain owns business meaning and business records.

~~~text
CLIENT DOMAIN                          FLOW

Orders, cases, assessments             Definitions and versions
Requirements and evidence              Workflow instances
Products and customers                 Node instances
Business decisions                     FSM transitions
Findings and issues                    Assignments and attempts
Payments and shipments                 Signals and timers
Domain audit                           Execution audit
                                       Inbox and outbox
~~~

When a client-specific term appears in an example, it is an opaque business
reference from Flow's perspective.

Embedded, that boundary is a convention rather than a process wall: nothing
physically stops a query joining a workflow table to a domain table. Keeping
`workflow_core` free of domain knowledge, and keeping such joins out of review,
is what preserves it.
