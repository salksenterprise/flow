# Flow Technical Design

> **Superseded.** ISRP now owns its orchestration directly; there is no separate
> workflow engine, service or package. This document described that engine and is
> retained only until its content has been folded into the ISRP design set. See
> [ISRP Orchestration](isrp/orchestration.md) for the current design.

Status: Draft for review.

This document describes how Flow works. The [charter](charter.md) says why it
exists and what was decided; the [requirements](requirements.md) say what it
must do and whether each part is proven. This one says how.

It supersedes [the Release 1-3 specification](workflow-engine/specification.md)
for everything below. That document describes Flow as a standalone service with
several behaviours that have since changed, and it is retained only as a record
of the earlier design.

## How to read this

Sections cite requirement identifiers such as `EMB-3` or `EXE-9`. Every
identifier resolves to a row in the requirements document with a status, and to
at least one test when that status is `Done`.

Nothing here is aspirational unless it is marked so. Where behaviour is
implemented but uncovered by a test, or not implemented at all, the section
says so in place rather than at the end, because a reader who stops halfway
should not be misled by the half they read.

The system as described is 1,601 lines of core, 1,643 lines of SQLite adapter,
463 lines of HTTP shell, and 145 tests.

# Part I: Shape

## 1. Components and the direction of dependency

~~~text
        host application
                |
                | owns the connection, the transaction, the identity
                v
  +---------------------------+
  |      workflow-core        |   pure Python, standard library only
  |                           |
  |  engine     driver, FSM   |
  |  states     categories    |
  |  rules      guards        |
  |  calendars  deadlines     |
  |  actor      identity      |
  |  validation publication   |
  |  ports      the interface |
  +-------------+-------------+
                |
                | repository port, 54 operations
                v
  +---------------------------+
  |     workflow-sqlite       |   development adapter
  |     workflow-oracle       |   production adapter, not built
  +---------------------------+
~~~

Dependencies point inward. `workflow_core` imports `contextlib`, `dataclasses`,
`datetime` and `typing`, and nothing else (`EMB-11`, `NFR-1`, asserted by
inspection and enforceable in CI). The adapter depends on the core, because an
adapter implements the core's port and raises the core's errors. The core never
depends on an adapter.

Two consequences follow. A second adapter is a new package, not a change to the
engine. And the engine can be reasoned about without a database in mind, which
is why the driver is testable at all.

## 2. Two deployment modes, one core

~~~text
EMBEDDED                              SERVICE SHELL

host process                          FastAPI process
  host code                             HTTP endpoints
  workflow-core                         workflow-core
  adapter                               adapter
  host's database                       Flow's database
                                              ^
                                              |
                                        HTTP clients
~~~

Embedded is the default and the mode Flow is designed for. The service shell
exists for callers that cannot embed: another language, another process
boundary, an existing integration.

They share the core, the adapter and the contract suite. The shell adds
request parsing, error-to-status mapping and identity resolution, and nothing
else. It contains no orchestration logic, so a behaviour proven in one mode
holds in the other (`EMB-12`).

## 3. Module map

| Module | Lines | Responsibility |
|---|---:|---|
| `workflow_core/engine.py` | 1003 | Commands, the driver, FSM transitions, repair, migration, the shared log |
| `workflow_core/validation.py` | 150 | Publication-time checks |
| `workflow_core/rules.py` | 129 | The guard language |
| `workflow_core/calendars.py` | 94 | Working-time deadlines |
| `workflow_core/ports.py` | 81 | The repository interface |
| `workflow_core/actor.py` | 58 | Identity as a value |
| `workflow_core/states.py` | 46 | Execution categories and policy vocabularies |
| `workflow_core/errors.py` | 30 | The error contract |
| `workflow_sqlite/repository.py` | 1433 | The adapter |
| `workflow_sqlite/schema.py` | 206 | 25 tables, 15 indexes, the migration chain |
| `workflow-api/app/main.py` | 216 | HTTP endpoints |
| `workflow-api/app/security.py` | 49 | Identity from a trusted header |

# Part II: The embedding contract

The differentiating part of the design. `examples/embedded-host/host.py` is the
reference integration and `tests/test_embedding.py` holds the contract.

## 4. Who owns the connection

The host does (`EMB-1`). A repository constructed without a path never opens a
connection and says so if asked:

~~~python
repository = SQLiteWorkflowRepository()          # embedded: no path
engine = WorkflowEngine(repository)

with repository.using(connection):               # the host's connection
    engine.apply_action(step_id, command)
~~~

`using()` binds a connection for the duration of a block, sets the row factory
Flow needs, restores it on exit, and refuses a second binding on the same
thread. The binding is thread-local, so one repository object serves concurrent
host threads each with its own connection (`EMB-10`).

Constructed with a path, the repository opens and commits its own connections.
That is the service shell's mode and the mode the tests use for convenience.

## 5. The transaction protocol

Flow never calls commit, rollback or close on a host connection (`EMB-2`). The
engine's own `transaction()` is re-entrant: with a connection already bound, it
yields without opening or committing anything.

~~~text
host                          Flow

BEGIN IMMEDIATE
   |
   | INSERT INTO approval_request ...
   |
   +--> with repository.using(connection):
   |        engine.apply_action(...)
   |            transaction() sees a bound connection and yields
   |            writes step, event, outbox row
   |        returns; connection still in transaction
   |
COMMIT        <- the host decides, and both sides land together
~~~

The payoff is `EMB-3`. A domain write and a workflow transition commit as one
unit, so they cannot disagree. Across a service boundary the same operation
needs a saga, a compensating action and a reconciliation job.

Two tests pin it. One aborts the host transaction after both writes and asserts
neither survives. The other has a second connection watch while the host holds
a transaction open, and asserts Flow's rows stay invisible until the host
commits — direct evidence rather than inference.

A further consequence is `EMB-13`: the host's table can carry a real foreign key
into `workflow_instance`, enforced by the database. A separate service can only
offer an opaque identifier that nothing validates.

## 6. Installing the schema

`create_schema(connection)` runs from the host's migration step, not at import
and not inside a business transaction (`EMB-6`, `EMB-7`). It refuses an open
transaction outright, because DDL commits implicitly in SQLite and would commit
whatever the host had in flight.

It runs in three phases, and the order matters:

~~~text
1. detect a legacy database   workflow_instance exists, schema_metadata does not
2. create tables              CREATE TABLE IF NOT EXISTS, all 25
3. migrate, once only         only when step 1 said legacy and no version stamp
4. stamp the schema version   schema_metadata.schema_version
5. create indexes             all 13
~~~

Indexes come last because on a legacy database some of them reference columns
that step 3 has only just added. An earlier attempt to create them alongside
the tables broke every upgrade, which is why `test_emb7_legacy_database_
migrates_once_and_then_stops` exists.

The version stamp is what makes the whole thing idempotent. The legacy migration
rewrites state derived from column values; running it a second time against live
rows corrupted completed nodes. It now runs once, for a database that predates
stamping, and never again.

## 7. Namespacing Flow's tables

A host may already have a table called `workflow_instance`. `EMB-5` allows a
prefix:

~~~python
SQLiteWorkflowRepository(path, table_prefix="flow_")
~~~

Rather than template a hundred inline statements, the `db` property returns a
wrapper that rewrites object names on the way to the database. With no prefix
configured it returns the bare connection, so the default path carries neither
overhead nor risk.

The rewrite is a regular expression over the 25 known table names plus
`idx_*`, anchored with word boundaries. `workflow_instance` matches;
`workflow_instance_id` does not, because `_` is a word character. That property
is asserted directly rather than assumed.

## 8. Background work

Timers, automation jobs and event delivery need something to drive them. Flow
supplies the routines; the host supplies the schedule (`EMB-9`).

~~~text
engine.process_due_timers()        fires timers that are due
engine.claim_automation_jobs(id)   hands work to a worker
repository.pending_outbox()        claims events for delivery
~~~

Each is an ordinary call that can run inside the host's transaction. There is no
thread, no background loop and nothing that starts itself. A host with a
scheduler calls them from it; a host without one calls them from a cron entry.
The service shell's worker process is one such caller, not a privileged one.

This is the part of embedding that costs the host something, and the design does
not pretend otherwise: the host takes on a scheduling responsibility it would
not have had against a standalone service.

## 9. The error contract

Anything arising from workflow data or workflow state raises a `WorkflowError`,
so a host can catch one type and roll back (`EMB-4`):

~~~text
ValidationError    malformed request or definition
ConflictError      well formed, not allowed in the current state
NotFoundError      no such workflow, step or job
ExecutionError     the driver could not reach a stable state
~~~

A misconfiguration raises `RuntimeError` instead — no database path, a call
outside a transaction, `create_schema` inside one. That is a programming mistake
in the host, and it should not be swallowed by a handler written for business
failures.

The service shell maps them to 400, 409, 404 and 500 respectively.

# Part III: The execution model

## 10. Three dimensions of state

The design decision the rest of the engine follows from.

~~~text
WORKFLOW INSTANCE
    |
    +-- lifecycle_state        business-facing, from the pinned lifecycle FSM
    +-- execution_status       RUNNING SUSPENDED COMPLETED FAILED CANCELLED
    |
    +-- STEP INSTANCE
          +-- state            from the pinned step FSM
          +-- execution_status the engine's view of the node
~~~

A lifecycle of `FULFILLING` says where the business process is. An execution
status of `RUNNING` says orchestration is live. A node state of `IN_PROGRESS`
says what is happening inside one activity. Collapsing these produces the
enormous single FSM that makes engines like jBPM hard to follow.

The `status` column on `workflow_instance` is a compatibility projection of
`execution_status` and nothing more. It once reported `ACTIVE` for a cancelled
workflow; it now maps terminal statuses through faithfully.

## 11. Execution categories

A node's FSM state belongs to the definition. Its execution status belongs to
the engine. The bridge is a declared category (`EXE-13`):

~~~json
{"key": "PARKED", "category": "WAITING"}
~~~

~~~text
NOT_READY  READY  ACTIVE  WAITING  COMPLETED  SKIPPED  FAILED  CANCELLED
~~~

`resolve_category` takes the declaration when present, falls back to a table of
known state names, and otherwise uses whether the state is terminal. An unknown
category is rejected at publication.

This replaced a ladder of name matching in `apply_action` — `"FAILED" if target
== "FAILED" else "CANCELLED" if ...`. Declaring the category did not add
machinery; it deleted some, and made the two features below straightforward
rather than special cases.

## 12. The driver

`_drive` runs to a fixed point, bounded at 200 iterations. Exceeding the bound
raises `ExecutionError` rather than hanging the transaction (`NFR-7`,
implemented, not covered by a test).

~~~text
repeat until nothing changes, or 200 times:

  reload the workflow; stop unless RUNNING

  for each NOT_READY node:
      can activate?        -> activate
      otherwise dead?      -> skip

  for each READY node, by type:
      FORK JOIN MILESTONE  -> complete immediately
      END                  -> complete the workflow, drive the parent, return
      WAIT_SIGNAL          -> wait, and consume a signal already stored
      AUTOMATED_TASK       -> enqueue a job, wait
      TIMER                -> schedule a durable timer, wait
      SUBWORKFLOW          -> wait, creating a configured child if declared

  for each WAITING node:
      WAIT_SIGNAL          -> consume a matching signal if one has arrived
      AUTOMATED_TASK       -> complete if its job has since succeeded
      SUBWORKFLOW          -> complete or fail on child policy
~~~

The `WAITING` pass is what makes the engine recover rather than stall. A job
that finished while its workflow was suspended is reconciled here on resume,
which is why job completion does not have to advance the node itself.

Every command runs the driver before committing, so a workflow is always
observed at a fixed point, never mid-cascade.

## 13. Activation and joins

A node activates when its applicable predecessors are satisfied. An edge is
applicable when its condition evaluates true against the current facts.

| Rule | Satisfied when |
|---|---|
| `ALL` | every applicable predecessor is satisfied |
| `ANY` | at least one applicable predecessor is satisfied |
| `N_OF_M` | at least `configuration.required_count` are satisfied |
| `ALL_REQUIRED` | every predecessor is satisfied, applicable or not |

`ALL_REQUIRED` is the one that differs from the old specification, where it was
indistinguishable from `ALL` (`EXE-7`). It now waits for the whole fan-in
rather than only the live paths, which is what you want when a branch's outcome
matters even if its edge was not selected. Branches that were never taken reach
`SKIPPED` on their own, so it does not deadlock.

A predecessor counts as satisfied when its execution status is `COMPLETED` or
`SKIPPED`, or when it is `FAILED` under a `CONTINUE` failure policy.

An actor holding `workflow.override` may force a join past its rule, with a
mandatory reason, recorded as an event.

## 14. Dead branches

A node is dead when every predecessor has reached a terminal execution status
and it still cannot activate (`EXE-9`). It is then marked `SKIPPED`.

Without this, a node on a branch that was never taken stayed `NOT_READY` for the
life of the workflow, and any progress count derived from it was wrong.

Skipping sets execution status only. The FSM state is the definition's to
control, and a custom step FSM need not declare a skipped state or a transition
into one. The same restraint applies to cancellation, and it is the reason a
cancelled node can read `state=READY, execution_status=CANCELLED`. That is
honest rather than tidy, and a console should render the category.

## 15. Failure

A failed node applies a declared policy (`EXE-10`):

| `on_failure` | Effect |
|---|---|
| `FAIL_WORKFLOW` (default) | workflow goes to `FAILED` |
| `SUSPEND` | workflow goes to `SUSPENDED` for intervention |
| `CONTINUE` | the node stays failed; the graph proceeds around it |

Previously a failed node left the workflow `RUNNING` with nothing able to
advance it — indistinguishable, from outside, from a workflow merely waiting.

All three failure routes converge on one handler: a step action that reaches a
`FAILED` category, an automation job that exhausts its attempts, a subworkflow
whose required child failed, and a service-level breach configured to fail.

A parent whose required child fails applies `child_failure_policy`, either
`FAIL` (default) or `CONTINUE`, the latter completing the parent node once every
awaited child has settled either way (`EXE-15`).

## 16. Node types

| Type | On activation | Completes when |
|---|---|---|
| `HUMAN_TASK` | assignment and candidates created | an actor drives its FSM to a terminal state |
| `DECISION` | as above | as above |
| `AUTOMATED_TASK` | durable job enqueued | a worker reports success |
| `FORK` | — | immediately |
| `JOIN` | — | immediately once its rule is satisfied |
| `SUBWORKFLOW` | optional child created | awaited children satisfy policy |
| `WAIT_SIGNAL` | waits | a matching signal is consumed |
| `TIMER` | durable timer scheduled | the timer fires |
| `MILESTONE` | — | immediately |
| `END` | — | immediately, completing the workflow |

# Part IV: Definitions

## 17. Immutability and publication

A published version never changes (`DEF-2`). Republishing the same version
number is a `ConflictError`. A running instance stays pinned to the version it
started on, and publishing a new version does not disturb it (`DEF-3`).

Publication validates, and refuses (`DEF-4` through `DEF-8`):

~~~text
a graph that is cyclic, rootless, unreachable, or cannot reach an END
an unsupported node type, a duplicate edge, an edge out of an END
a JOIN without a rule, a WAIT_SIGNAL without a signal type,
    a TIMER without a delay
an FSM with duplicate or dangling transitions
a state declaring an unknown execution category
an unknown failure policy, child failure policy or breach action
a malformed business calendar
a guard naming an operator Flow does not implement
~~~

The last one mattered more than it sounds. An unknown operator used to evaluate
false forever, silently routing every workflow down the wrong branch.

## 18. The guard language

Rules are data. A definition cannot execute code (`DEF-1`).

~~~text
eq  ne  in  not_in  exists  truthy
gt  gte  lt  lte  contains  starts_with
composed with all, any, not
~~~

Fields are dotted paths, so `subject.classification` reads a nested fact
(`EVT-4`). A missing path is distinguishable from a null value, which is what
lets `exists` mean anything.

Every operator is total: it answers true or false for any pair of values rather
than raising. A guard is evaluated mid-transaction, and a type mismatch must not
be able to crash a workflow. Comparing a string to a number is false, not an
error.

## 19. Version migration

Publishing never migrates anything. Migration is an explicit command, and the
policy is deliberately narrow (`DEF-9`), because silently remapping work that is
in someone's hands is worse than refusing:

1. Permissioned (`workflow.migrate`) and reasoned.
2. Within one workflow definition, to a published version.
3. Refused while any node is `ACTIVE` or `WAITING`.
4. Nodes map by step key. Shared keys keep their state, removed nodes are
   retired, new nodes start `NOT_READY`.
5. Refused if the current lifecycle state does not exist in the target's
   lifecycle FSM, which would otherwise strand the workflow.

Step key is the identity that survives a version change. Node identifiers do
not, and node order certainly does not.

# Part V: Work

## 20. Candidates and assignment

A node may declare candidate users, roles, groups and organizations. Claiming is
refused for an actor outside the candidate set, and organization is matched as
well as membership, so an `ORG-A` reviewer cannot claim an `ORG-B` node even
holding the right group (`WRK-1` to `WRK-3`).

Reassignment never overwrites. The previous assignment is marked `REPLACED` with
an end timestamp and the new one is inserted, so the history of who held a piece
of work survives (`WRK-4`, implemented, not covered by a test).

## 21. Service levels and calendars

A node may carry `due_in_seconds`. On activation the engine computes a deadline,
schedules a durable timer with action `SLA_BREACH`, records the due time on the
assignment, and emits `STEP_DUE_AT_SET` (`WRK-8`).

When the timer fires and the node is still open, the breach is always recorded.
What follows is the definition's choice, because whether a late review escalates
or fails is a business decision rather than an engine one:

| `on_breach` | Effect |
|---|---|
| `NOTIFY` (default) | event only |
| `ESCALATE` | reassign to `escalate_to`, recorded |
| `FAIL` | the node fails, and its failure policy applies |

A node that finished before its timer fires is left alone.

Deadlines may respect a business calendar (`JOB-7`):

~~~json
{"business_days": [0, 1, 2, 3, 4],
 "opens_at": "09:00", "closes_at": "17:00",
 "holidays": ["2026-12-25"]}
~~~

Four working hours from Friday afternoon land on Monday morning, not Saturday.
Without this, every deadline set late on a Friday breaches over the weekend and
the breach tells an operator nothing. Omit the calendar for plain elapsed time.

The resolver walks forward one working day at a time, bounded, so a calendar
that can never satisfy a deadline raises rather than loops. Its arithmetic is
pure and tested without a database.

## 22. Work queries

`list_work` answers the two questions a queue screen asks (`WRK-6`, `WRK-7`):

~~~text
assignee=alice     what is already alice's
actor={...}        what alice could claim, by candidate rules
~~~

with further filters on execution status, node type, business type, business key
and workflow, and `limit`/`offset` with a stable total. Results are indexed on
`work_assignment(assignee, status)` and
`step_instance(execution_status, workflow_instance_id)` (`NFR-6`).

Before this, a client had to fetch whole workflows and sift them, which is why
the console could not show anyone their own queue.

# Part VI: Reliability

## 23. Idempotency

Every command carries a client-generated `command_id`. A repeated identifier has
one effect.

What it returns is a decision, recorded in the charter and reversible there. A
repeat returns the workflow's **current state**, and storage holds a receipt:

~~~json
{"workflow_instance_id": 42, "action": "complete", "revision": 7}
~~~

The alternative — storing the original response verbatim — preserved the letter
of "returns the original result" but made command storage grow with the square
of the number of commands, since each response embedded the full workflow
including every event. A caller retrying after a timeout also usually wants to
know where things stand now, not what was true before the timeout (`OPS-7`).

The check runs before revision validation. In the other order, a retry of a
command that had already succeeded returned a revision conflict, which is the
one answer a retrying client cannot act on (`REL-2`).

## 24. Optimistic revisions

A client may pass `expected_revision`. A mismatch is a `ConflictError` with no
partial write (`REL-3`). The revision increments once per command, after the
driver settles.

## 25. One event log, one outbox

Every runtime change writes an ordered event and an outbox row in the same
transaction (`REL-4`, `REL-5`). Sequence numbers are dense and unique per
workflow, which is what makes a consumer able to detect a gap.

~~~text
BEGIN
  update runtime state
  append event_log        (sequence_number = max + 1 for this aggregate)
  insert outbox_event     (same event_id, full envelope)
  record the command receipt
COMMIT
        |
        v
  delivery worker
        +-- claim, sign, POST
        +-- delivered      -> per-subscription record
        +-- failed         -> PENDING with exponential backoff
        +-- exhausted      -> DEAD_LETTER
~~~

Each envelope carries `schema_version`, so a consumer can tell which shape it is
reading (`REL-8`).

Retry redelivers only to subscriptions that have not already accepted the event
(`REL-7`). Previously one failing subscriber produced duplicate deliveries to
every healthy one on each retry.

Signing secrets are write-only. Only the delivery worker reads them back; no
read interface returns one (`OPS-5`).

### The host shares both

An embedding host has domain events of its own, and the obvious thing is for it
to build a second event table, a second outbox and a second delivery worker
beside Flow's. That doubles the operational surface of a system whose whole
argument is having fewer moving parts, and it gives two orderings that cannot be
reconciled.

So the log is not workflow-scoped. It is scoped by aggregate, and Flow is one
aggregate type among the host's (`EMB-14`):

~~~text
event_log
  aggregate_type   aggregate_id   sequence_number   event_type
  WORKFLOW         42             7                 STEP_COMPLETE
  ISRP_ASSESSMENT  ASMT-1002      3                 RESPONSE_SUBMITTED
  ISRP_FINDING     FND-9          1                 FINDING_CREATED
~~~

`UNIQUE(aggregate_type, aggregate_id, sequence_number)` keeps each stream dense
independently, so gap detection works for the host's events exactly as it does
for Flow's. The workflow foreign key is retained as a nullable column, so Flow's
own rows stay referentially safe while a host row that has no workflow is still
welcome.

The host writes through the engine rather than with its own SQL, because
sequence allocation and the event-to-outbox pairing are the two things that must
not be reimplemented:

~~~python
engine.record_event(
    "ISRP_ASSESSMENT", "ASMT-1002", "RESPONSE_SUBMITTED",
    actor=current_user, correlation_id="ISR-100", new_revision=4,
    changed_fields={"response_status": "SUBMITTED"},
)
~~~

Called inside the host's transaction, a domain event and a workflow transition
commit together or not at all (`EMB-15`). The outbox row carries
`aggregate_type`, `aggregate_id`, `aggregate_version` and `correlation_id`, so
one delivery worker serves both and a projector can filter to the streams it
cares about (`EMB-16`).

`WORKFLOW` is reserved. A host that passes it is refused, because a second
writer allocating sequence numbers in Flow's own stream would corrupt the
ordering Flow depends on (`EMB-18`).

One consequence worth stating: Flow's composite key is
`(aggregate, sequence)`, not the `(aggregate, version, event_type)` a host might
use to guarantee emit-once. Flow emits several events at one revision — a single
drive can activate three nodes — so that constraint cannot hold globally. A host
wanting emit-once derives `event_id` deterministically from its own key instead;
`UNIQUE(event_id)` then gives the identical guarantee without constraining
Flow.

## 26. One inbox

Provider events are deduplicated on `connector_name + provider_event_id`, then
translated into signals through the same idempotent command path (`EVT-8`,
`EVT-9`). The raw receipt is persisted before domain handling, so a translation
failure leaves evidence.

Signals are durable and order-independent. One arriving before its `WAIT_SIGNAL`
node activates is stored and consumed when the node appears; one arriving while
the node waits is consumed immediately; either way exactly once (`EVT-5`,
`EVT-6`).

The host shares this table too (`EMB-17`). `record_inbox_event` accepts a
provider event that has not yet been correlated to any workflow, which
`ingest_external_event` cannot, and `claim_inbox_events` lets one connector
runner drain receipts for the host and for Flow alike. The deduplication key is
the same either way, so a provider that delivers one event twice is absorbed
once regardless of which side consumes it.

## 27. Claiming

Three kinds of worker take work, and each claim is guarded.

~~~text
automation jobs   UPDATE repeats the claim condition; rowcount decides
                  lease expiry permits reclaim
timers            status transition, filtered to RUNNING workflows
outbox            claim with stale-claim recovery after five minutes
~~~

Job claiming previously selected candidate rows and then updated them without
re-checking, so two workers could hold one job. The `UPDATE` now repeats the
condition and a rowcount of zero means someone else won (`JOB-3`).

Jobs and timers are both filtered to `RUNNING` workflows, so a suspended
workflow neither hands out work nor advances (`EXE-12`).

## 28. Failure and recovery

| Situation | Outcome |
|---|---|
| Host transaction fails before commit | Everything rolls back, Flow included |
| Response lost after commit | The retry returns current state, no second effect |
| Automation worker dies | The lease expires and another worker reclaims |
| Webhook endpoint down | Backoff, then dead letter, then redrive when fixed |
| Duplicate provider event | The unique key stops the second; the signal is idempotent |
| Process restarts | Timers and jobs are durable; no state is rewritten |
| Node fails | Its declared policy decides; the workflow does not sit still |
| Workflow wedged anyway | `stuck_workflows` reports it |

The restart row is the one that used to be false. A legacy migration ran on
every start and rewrote node state from column values; a completed node in a
custom FSM reverted to active and any join awaiting it could never be satisfied.

# Part VII: Security

## 29. Identity

`Actor` is a frozen dataclass carrying an id, a type, an organization, roles,
groups and permissions. It accepts a bare string, a mapping or another `Actor`,
so the shape a caller uses does not reach the engine (`EMB-8`).

Flow never reads a credential, a token or a header. It is handed an identity and
trusts the caller to have established it.

## 30. Where authority comes from

Embedded, the question does not arise: the host authenticated the caller and
passes what it knows.

The service shell has no such luxury, and this was the sharpest defect in the
original design — permissions arrived in the request body, so any caller could
assert `workflow.override` (`NFR-5`).

~~~text
gateway                     service shell

authenticate           ->   read the trusted header
inject x-flow-actor         build the actor from it
                            discard whatever the body claimed
                            no header -> anonymous, no permissions
~~~

The request model can no longer express a permission at all. With no gateway
configured the caller is anonymous and holds nothing, so a misconfigured
deployment fails closed rather than open. This is proven through the running
application, not against the helper alone: a request carrying
`permissions: ["workflow.close"]` in its body is refused, and the same request
with the header succeeds.

## 31. Permissions in use

~~~text
workflow.override    force a join past its rule
workflow.repair      manual intervention on a node
workflow.migrate     move an instance to another version
<definition's own>   any transition declaring required_permission
~~~

Each also demands a reason, and each is recorded as an event. The permission
check on definition-declared transitions was the bug that shipped: a stray
character made every permissioned lifecycle transition raise `TypeError`, for
the permitted actor as well as the refused one, because no test covered one
(`EXE-3`).

# Part VIII: Persistence

## 32. The repository port

54 operations, defined as a `Protocol` in `workflow_core/ports.py`. The engine
calls nothing else. Grouped: definitions and versions, FSM lookup, command
receipts, workflow and node runtime, facts, candidates and assignments,
attempts, signals and inbox, jobs, timers, events, queries, operations,
delivery.

## 33. Schema

25 tables, 15 indexes.

~~~text
definitions   fsm_definition fsm_version fsm_state_definition
              fsm_transition_definition workflow_definition workflow_version
              step_definition transition_definition

runtime       workflow_instance workflow_subject workflow_fact_history
              step_instance step_attempt work_assignment work_candidate

async         signal_receipt automation_job durable_timer

reliability   event_log workflow_command inbox_event outbox_event
              webhook_subscription outbox_delivery

meta          schema_metadata
~~~

The three shared with an embedding host are `event_log`, `outbox_event` and
`inbox_event`. See section 25.

Conventions, chosen for portability to Oracle:

1. Timestamps are one format everywhere: `YYYY-MM-DDTHH:MM:SS.mmmZ`, produced
   identically by the application and by the column default (`NFR-8`). Mixing
   `isoformat()` with `CURRENT_TIMESTAMP` had put two shapes in one column, and
   due-time comparisons are string comparisons.
2. States and types are bounded strings, not database enums.
3. JSON lives in `_json` columns, decoded by the adapter.
4. Mutable aggregates carry a revision.
5. Business rules live in services, not triggers or stored procedures.

## 34. The adapter contract

`tests/contract.py` holds fourteen behaviours every adapter must provide
(`NFR-2`). It goes through the repository interface and the engine, never
through dialect-specific SQL, so it is not a SQLite test that Oracle must
somehow pass.

~~~text
schema creation is idempotent
a failed transaction discards its writes
a command receipt round-trips
event sequence is dense and ordered per workflow
every event writes an outbox row
a job is claimed by exactly one worker
an expired lease is reclaimable
suspended work is not handed out
a signal is consumed exactly once
fact history records previous and new values
candidate eligibility is organization-aware
work queries filter and paginate
operational counters are reported
outbox claim is recoverable and dead-letters
~~~

Certifying an adapter is a subclass:

~~~python
class OracleContractTests(RepositoryContract, unittest.TestCase):
    def make_repository(self):
        return OracleWorkflowRepository(dsn=os.environ["ORACLE_DSN"])
~~~

## 35. Oracle: what will have to change

Oracle is the production adapter (charter decision). It is not built. From the
SQLite implementation, these are the points that will not port unchanged:

| Concern | SQLite today | Oracle |
|---|---|---|
| Write serialization | `BEGIN IMMEDIATE` | row locks; `SELECT ... FOR UPDATE SKIP LOCKED` for claiming |
| Identity | `AUTOINCREMENT` | identity columns or sequences |
| JSON | `TEXT` with application encoding | `JSON` or `CLOB` with a check |
| Upsert | `ON CONFLICT DO UPDATE` | `MERGE` |
| `INSERT OR IGNORE` | native | `MERGE`, or catch the constraint |
| Timestamps | `TEXT`, one format | `TIMESTAMP WITH TIME ZONE`, format at the boundary |
| Booleans | integers | `NUMBER(1)` or a check constraint |
| Concurrency | one writer | genuine concurrent writers, which the contract suite must then exercise harder |

The last row is the one to take seriously. SQLite's single-writer model hides
races that Oracle will expose. The claiming tests in the contract suite are
written with that in mind, but they will need to run with real parallelism
before anyone should trust them.

# Part IX: Operations

## 36. What an operator can see and do

~~~text
GET  /api/operations/counters              queue depth, failures, lag
GET  /api/operations/stuck                 wedged workflows
GET  /api/operations/dead-letters          exhausted deliveries
POST /api/operations/dead-letters/{id}/redrive
POST /api/steps/{id}/repair                skip, force-complete, retry, reassign
GET  /api/work                             the queue, filtered and paginated
~~~

Counters cover running, suspended, failed and stuck workflows; queued, running
and lease-expired jobs; due timers; pending and dead-lettered outbox rows; and
failed inbox rows (`OPS-6`).

A stuck workflow is one that is `RUNNING` with no node ready, active or waiting,
no job queued or running, and no timer scheduled (`OPS-2`). The engine should
never produce one. The detector exists for when a bug or a crash does, because
nothing else in the system reports it.

Repair deliberately bypasses the step FSM, because it exists for the situations
the definition did not anticipate (`OPS-3`). That is why it demands
`workflow.repair`, a reason, and records every use. A repair other than
reassignment also returns a failed or suspended workflow to `RUNNING`, since
intervening implies an intent to continue.

# Part X: What this design does not do

## 37. Implemented but unproven

Thirteen requirements are implemented with no test citing them. They may well
work; they are not evidence. Named here rather than buried, because the pattern
that produced the original defects was exactly this:

~~~text
DEF-5 DEF-6 DEF-7   publication rejections beyond those already tested
EXE-4 EXE-5         FSM-declared reason and guard enforcement
WRK-4 WRK-5         assignment history, work iterations
JOB-4 JOB-5 JOB-6   retry backoff, timer survival across restart,
                    one firing per iteration
REL-6 REL-9         delivery backoff, stale claim recovery
NFR-7               the driver's iteration bound
~~~

## 38. Blocked

`NFR-3`, the contract suite running against the production database in CI, needs
an Oracle instance and a pipeline. Neither exists yet.

## 39. Deliberately absent

~~~text
cross-application parent-child workflows   embedded mode cannot span databases
a second runtime implementation            Python only, though the definition
                                           format carries no Python semantics
graphical definition authoring             out of scope
AI-assisted execution modes                out of scope
a business rules engine                    guards are for routing, not policy
an identity provider                       Flow is handed an identity
~~~

The first is the real cost of embedding, and it should be stated plainly rather
than discovered later. A workflow embedded in one application's database cannot
orchestrate a child in another's. Processes that genuinely span services need
the service shell, or a different tool.

## 40. Known rough edges

1. A cancelled or skipped node keeps its FSM state while its execution status
   changes, so it can read `state=READY, execution_status=CANCELLED`. Correct,
   but a console must render the category rather than the state.
2. Standalone reads take a write lock, because `BEGIN IMMEDIATE` is used for
   every transaction. Correct and simple; if read throughput matters, a
   read-only transaction variant is the fix.
3. The operator console is still the original single-file React application. It
   hardcodes the default FSM's actions, so a custom step FSM renders no buttons,
   and it does not use the work queue or operations endpoints.
4. Three things the ISRP design assumes are not in Flow: `BLOCKED` as an
   execution category, an execution-mode field that publication can validate,
   and an FSM transition that returns to the previous state. Each is recorded
   as an open dependency in `docs/isrp/architecture.md` with a workaround.

## Traceability

~~~text
charter.md             why, and what was decided
requirements.md        what, with a status and a citing test
technical-design.md    how
tests/                 the evidence
~~~

Every requirement identifier in this document resolves to a row in the
requirements document. Every row marked `Done` there resolves to a test that
names it. Where this document describes behaviour that is not yet proven, it
says so in the section that describes it.
