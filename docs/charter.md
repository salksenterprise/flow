# Flow Product Charter

Status: Draft for review.

This charter sets the product direction for Flow. It supersedes the positioning
in the Releases 1-3 documents, which describe Flow as a standalone service. The
engine described there is retained; the way applications consume it changes.

## Problem

Teams in this organization run long-lived processes that outlive a single
transaction: security reviews, assessments, approvals, onboarding, fulfillment.
Each team re-implements the same substrate by hand. State machines become
nested conditionals. Assignment becomes a nullable `assigned_to` column. Retry
becomes a cron job. Audit becomes whatever the table had room for. The work is
repeated, inconsistent, and rarely correct under failure.

The available alternative has not succeeded here. jBPM asks a team to operate a
second engine with its own deployment lifecycle, its own state store, and its
own tooling, positioned at arm's length from the code that performs the work.
The cost of that distance exceeds the value of the orchestration for most
processes. Commercial and hosted durable-execution platforms are not available
in this organization.

## Product

Flow is an embeddable workflow execution core.

An application adds Flow as a library, declares its process as data, and calls
the engine inside its own database transaction. There is no engine to deploy,
no second database, no network hop, and no separate release train.

~~~text
WITHOUT FLOW                        WITH FLOW EMBEDDED

Application                         Application
  hand-rolled state machine           + flow-workflow-core
  hand-rolled assignment              + flow adapter for its database
  cron-driven retries                 declares a workflow definition
  ad hoc audit                        calls the engine in its own transaction
  one database                        one database
  one deployment                      one deployment
~~~

## Primary decision

Flow is consumed as an embedded library. The host application owns the process,
the database connection, the transaction boundary, and the authenticated actor.
An HTTP service shell wraps the same core for callers that cannot embed.

Consequences:

1. The host, not Flow, opens and commits the transaction. A domain write and a
   workflow transition commit together or not at all.
2. Flow's tables live in the host's database and may be referenced by real
   foreign keys rather than opaque identifiers.
3. Actor identity arrives in-process from code that has already authenticated
   it. Flow does not authenticate and does not accept self-asserted permissions.
4. Flow's background work (timers, automation jobs, event delivery) is driven by
   the host. Flow supplies the routine; the host supplies the schedule.
5. The service shell, the console, and the conformance suite all run against the
   same core through the same ports.

## Design principles

1. Simple enough to read in an afternoon. A developer should be able to follow
   the whole execution path without a diagram.
2. Definitions are data, never code. A workflow cannot execute arbitrary logic.
3. Explicit over implicit. Every state, transition, and failure mode is named.
4. Boring persistence. Plain tables, plain SQL, no ORM, no event-sourcing
   framework, no bespoke storage engine.
5. Nothing in the core knows any business domain.
6. Every exceptional operation is authorized, reasoned, and audited.

## Non-goals

Flow is not:

~~~text
a BPMN implementation
a low-code or graphical process designer
a business rules engine
a document, evidence, or content repository
an identity provider or authorization policy engine
a message broker
a cross-application orchestrator when embedded
a replacement for the host's domain model
~~~

The first item is deliberate. BPMN is a standard for drawing processes for
analysts. Flow targets developers who want a process executed correctly.

## Constraints

1. Python 3.11 or later for the embedded core.
2. The definition format must stay free of Python-specific semantics so that a
   second runtime implementation remains possible. No pickled callables, no
   dotted import paths as behavior, no language-dependent type coercion.
3. SQLite for development and tests. One production relational database, chosen
   before adapter work begins.
4. The core depends on the standard library and its repository ports only.
5. Additive schema migration. Published definitions are immutable.

## Success measures

Measured on the first embedding application, not on the engine in isolation.

~~~text
Integration effort       under 50 lines of host code to a running workflow
Time to first workflow   under 30 minutes from install, following the guide
Test friction            host test suite runs Flow with no container,
                         no network, and no background process
Operational surface      zero new deployable units for an embedding team
Correctness              no host-visible workflow state is lost or altered
                         by a process restart
~~~

## Out of scope for the first release

Cross-application parent-child workflows. Graphical definition authoring. A
second runtime implementation. AI-assisted execution modes. Any of these may
follow; none block the first release.

## Open questions

1. Which production database is authoritative. The Flow documents name Oracle
   first; the ISRP documents name PostgreSQL first. One must change.
2. Whether any embedding host will require a JVM runtime. Treated as Python-only
   for now, with the portability constraint above protecting the option.
3. Whether the operator console remains a Flow deliverable or becomes the host
   application's responsibility.
