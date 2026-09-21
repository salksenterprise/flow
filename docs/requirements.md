# Flow Requirements

Status: Draft for review. Target is the embedded core described in the
[charter](charter.md).

## How to read this document

Every requirement has a stable identifier. BDD scenarios and tests cite the
identifier, and the [definition of done](definition-of-done.md) forbids marking
a requirement `Done` without a test that names it.

Status values:

~~~text
Done       Implemented and covered by an automated test
Untested   Implemented, but no test cites it, so it is not trusted
Partial    Implemented with a stated limitation
Defect     Specified and implemented, but verified broken
Planned    Not implemented
~~~

`Untested` is the honest majority verdict, not a formality. The suite covers
the happy path of each headline feature and almost no failure path. An
`Untested` row may well work; it is simply not evidence.

`Defect` rows are the point of this document. The existing Releases 1-3
documents describe several of these behaviors in the present tense; they do not
work. Every `Defect` row below was reproduced against the current code.

## A. Embedding contract

The differentiating requirements. All are new.

| ID | Requirement | Status |
|---|---|---|
| EMB-1 | The host supplies an open database connection or session; Flow never opens one | Planned |
| EMB-2 | The host opens and commits the transaction; Flow calls no commit or rollback | Planned |
| EMB-3 | A domain write and a workflow transition in one host transaction commit atomically | Planned |
| EMB-4 | Flow raises typed errors the host can catch and roll back on; no bare exceptions escape | Partial |
| EMB-5 | Flow's tables carry a configurable name prefix to avoid collision with host tables | Planned |
| EMB-6 | Flow owns its own migration chain, runnable from the host's migration tool | Planned |
| EMB-7 | Schema creation is explicit and idempotent; importing Flow never mutates a database | Done |
| EMB-8 | Actor identity is a typed value passed in-process; Flow never parses credentials | Partial |
| EMB-9 | Background routines (timers, jobs, delivery) are callable functions the host schedules | Partial |
| EMB-10 | The core is safe to use concurrently from a multi-threaded host process | Done |
| EMB-11 | The core imports only the standard library and its own ports | Done |
| EMB-12 | Embedded and service modes run the same core through the same repository ports | Partial |

## B. Definitions

| ID | Requirement | Status |
|---|---|---|
| DEF-1 | A workflow definition is declarative data, incapable of executing arbitrary code | Done |
| DEF-2 | Published definition versions are immutable | Untested |
| DEF-3 | A running instance stays pinned to the version it started on | Partial |
| DEF-4 | Publication rejects graphs that are cyclic, unreachable, rootless, or cannot reach an end | Done |
| DEF-5 | Publication rejects unsupported node types, duplicate edges, and edges out of an end node | Untested |
| DEF-6 | Publication rejects a JOIN without a supported rule, a WAIT_SIGNAL without a signal type, and a TIMER without a delay | Untested |
| DEF-7 | Publication validates embedded lifecycle and step FSMs for duplicate and dangling transitions | Untested |
| DEF-8 | Publication rejects unknown guard operators rather than silently evaluating them false | Planned |
| DEF-9 | A defined migration policy moves an instance between definition versions | Planned |

## C. Execution

| ID | Requirement | Status |
|---|---|---|
| EXE-1 | Workflow lifecycle state, workflow execution status, and node state are separate dimensions | Done |
| EXE-2 | Lifecycle and node transitions are resolved from the pinned FSM version | Done |
| EXE-3 | A transition may require a permission, and is refused without it | Done |
| EXE-4 | A transition may require a reason, and is refused without it | Untested |
| EXE-5 | A transition may carry a guard evaluated against workflow facts | Untested |
| EXE-6 | The graph driver activates nodes whose applicable predecessors are satisfied, to a fixed point | Done |
| EXE-7 | Join rules ALL, ANY and N_OF_M are enforced against applicable predecessors | Partial |
| EXE-8 | Edge conditions select which predecessors are applicable | Done |
| EXE-9 | A node on a branch that can no longer run is marked skipped, not left pending forever | Planned |
| EXE-10 | A failed node drives the workflow to a declared failure outcome per its configured policy | Planned |
| EXE-11 | Terminating a workflow cancels its open nodes, timers, jobs and required children | Done |
| EXE-12 | A suspended workflow performs no node activation, timer firing, or job completion | Done |
| EXE-13 | Each FSM state declares its execution category rather than having one inferred from its name | Planned |
| EXE-14 | A parent SUBWORKFLOW node completes when its required children satisfy the configured policy | Done |
| EXE-15 | A child failure policy other than fail-the-parent is honored | Partial |
| EXE-16 | An authorized actor may override a join with a mandatory recorded reason | Done |

## D. Work and assignment

| ID | Requirement | Status |
|---|---|---|
| WRK-1 | A node may declare candidate users, roles, groups and organizations | Done |
| WRK-2 | Claiming is refused for an actor outside the candidate set | Done |
| WRK-3 | Candidate organization is enforced against the actor's organization | Done |
| WRK-4 | Reassignment preserves prior assignments as history rather than overwriting them | Untested |
| WRK-5 | A node supports repeated attempts with clarification and response cycles | Untested |
| WRK-6 | Work is queryable by assignee, candidate, state and business key | Planned |
| WRK-7 | Work queries are paginated and indexed | Planned |
| WRK-8 | A node may carry a due time, and breach raises a configured action | Planned |

## E. Facts, signals and external events

| ID | Requirement | Status |
|---|---|---|
| EVT-1 | Fact changes record previous value, new value, source, actor and revision | Untested |
| EVT-2 | Guards read facts through a constrained rule language with no code execution | Done |
| EVT-3 | The rule language supports equality, membership, existence and truthiness, composed with all/any/not | Done |
| EVT-4 | The rule language supports numeric comparison and nested field paths | Planned |
| EVT-5 | A signal arriving before its waiting node is stored and consumed when the node activates | Done |
| EVT-6 | A signal is consumed exactly once | Untested |
| EVT-7 | A repeated signal command returns the original result without a second effect | Done |
| EVT-8 | A provider event is deduplicated on connector name and provider event id | Done |
| EVT-9 | An accepted provider event is translated into a signal through the idempotent command path | Done |

## F. Automation and timers

| ID | Requirement | Status |
|---|---|---|
| JOB-1 | An automated node enqueues a durable job rather than executing logic inline | Done |
| JOB-2 | A worker claims a job under a lease; an expired lease permits reclaim | Partial |
| JOB-3 | Two workers cannot hold the same job simultaneously | Done |
| JOB-4 | A job retries with backoff to a bounded attempt count, then fails terminally | Untested |
| JOB-5 | A timer is durable and survives process restart | Untested |
| JOB-6 | A timer fires at most once per node iteration | Untested |
| JOB-7 | Timer due times may respect a business calendar | Planned |

## G. Reliability

| ID | Requirement | Status |
|---|---|---|
| REL-1 | A repeated command identifier returns the original result and causes no second effect | Done |
| REL-2 | Idempotency is checked before revision validation, so a retry never returns a conflict | Done |
| REL-3 | A mutation whose expected revision does not match current is refused with no partial write | Done |
| REL-4 | Every runtime change writes an ordered workflow event in the same transaction | Done |
| REL-5 | Every workflow event writes an outbox record in the same transaction | Done |
| REL-6 | Outbox delivery retries with backoff and moves to dead letter when exhausted | Untested |
| REL-7 | Retry redelivers only to subscriptions that have not yet succeeded | Done |
| REL-8 | Outbound messages carry a schema version | Planned |
| REL-9 | A stale worker claim is recovered | Untested |
| REL-10 | Restarting a process alters no workflow or node state | Done |

## H. Operations

| ID | Requirement | Status |
|---|---|---|
| OPS-1 | A workflow's full execution history is retrievable in order | Untested |
| OPS-2 | A workflow stuck with no active, waiting or pending work is detectable | Planned |
| OPS-3 | Authorized repair actions (retry, skip, force-complete, reassign) exist and are audited | Planned |
| OPS-4 | Dead-lettered events can be inspected and redriven | Planned |
| OPS-5 | Delivery secrets are never returned by a read interface | Done |
| OPS-6 | Counters for queue depth, timer lag, delivery lag and dead letters are exposed | Planned |
| OPS-7 | Command results are stored without embedding a full workflow snapshot | Defect |

## I. Non-functional

| ID | Requirement | Status |
|---|---|---|
| NFR-1 | The definition format contains no Python-specific semantics | Done |
| NFR-2 | Repository behavior is defined by a contract suite every adapter must pass | Planned |
| NFR-3 | The same contract suite runs against SQLite and the production database in CI | Planned |
| NFR-4 | A host test suite runs Flow with no container, network or background process | Partial |
| NFR-5 | No interface accepts self-asserted actor permissions | Defect |
| NFR-6 | Runtime queries used on a request path are indexed and bounded | Partial |
| NFR-7 | The graph driver terminates or raises rather than looping unbounded | Untested |
| NFR-8 | Timestamps use one representation throughout | Partial |


## Status summary

~~~text
Done        34
Untested    17
Partial     11
Defect       2
Planned     22
total       86
~~~

34 of 86 requirements are backed by a test, up from 24 before the P0 work.
The suite is 36 tests, up from 22. Coverage of failure paths is still thin:
guard refusal, retry exhaustion and lease expiry remain untested.

## Verified defects

All eleven rows were reproduced against the code, then written as failing tests
in `tests/test_p0_defects.py` before being fixed, per definition-of-done rule 3.
Nine are closed. Two remain open because each needs a decision rather than a
repair.

### Fixed

1. **EXE-3.** `engine.py:254` contained a stray `+` applied to a string, so
   every lifecycle transition declaring `required_permission` raised
   `TypeError` — for the permitted actor as well as the refused one. No test
   covered a permissioned lifecycle transition, which is why it shipped. Both
   the allow and the deny path are now tested.
2. **REL-10, EMB-7.** `initialize()` ran the legacy 0.2 migration on every
   process start, rewriting `step_instance.execution_status` from state names.
   A completed node in a custom FSM terminal state reverted to active and any
   join awaiting it could never be satisfied; the same statement overwrote a
   completed workflow's custom lifecycle state. Now gated by a
   `schema_metadata` version stamp, so the migration runs once against a
   legacy database and never again. A legacy-upgrade test asserts that a 0.2
   database still migrates and that a second start changes nothing.
3. **EMB-10.** The repository held its connection on the instance; four
   threads issuing concurrent starts produced 90 failures in 120 calls. Now
   thread-local connections with WAL, a busy timeout, and `BEGIN IMMEDIATE`
   so that two read-then-write callers cannot deadlock on lock upgrade.
4. **EXE-11.** After terminate, execution status read cancelled but the
   compatibility status field still read active and nodes remained ready.
   Terminating now cancels open nodes, timers, jobs and assignments, cascades
   to running children, and the projection reports cancelled.
5. **EXE-12.** A timer fired against a suspended workflow completed its node.
   Due timers and claimable jobs are both filtered to running workflows, and
   the driver reconciles a job that finished during suspension on resume.
6. **REL-2.** Retrying a child-workflow start with the same command identifier
   returned a revision conflict instead of the stored result. Idempotency is
   now checked before the revision is validated.
7. **REL-7.** A delivery retry re-posted to subscriptions that had already
   accepted the event. The worker now skips them.
8. **OPS-5.** The subscription read interface returned signing secrets. They
   are write-only now; only the delivery worker reads them back.
9. **JOB-3.** Job claiming selected candidate rows and then updated them
   without re-checking the claim condition, so two workers could hold one job.
   The update now repeats the claim condition and checks the rowcount.

### Open, pending a decision

10. **OPS-7.** Each command stores a full workflow snapshot including every
    event, so command storage grows quadratically. Fixing it requires choosing
    what a repeated command returns: the original result exactly as stored, or
    the workflow's current state. That is a contract change rather than a
    repair, so it belongs in the technical design document.
11. **NFR-5.** Actor permissions arrive in the request body, so any caller of
    the HTTP service can assert `workflow.override`. Embedding removes this
    outright, because identity arrives in-process from code that has already
    authenticated it. The service shell still needs a gateway or an
    authentication layer, which is a design decision, not a patch.

## Traceability

~~~text
Requirement ID
    -> BDD scenario tagged with the ID
         -> test that asserts it
              -> status column in this document
~~~

A requirement without a citing test may not be marked `Done`.
