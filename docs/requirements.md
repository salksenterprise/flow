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
Blocked    Cannot be closed here; the reason is stated below
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

The differentiating requirements. `examples/embedded-host` is the reference
integration and `tests/test_embedding.py` holds the contract.

| ID | Requirement | Status |
|---|---|---|
| EMB-1 | The host supplies an open database connection or session; Flow never opens one | Done |
| EMB-2 | The host opens and commits the transaction; Flow calls no commit or rollback | Done |
| EMB-3 | A domain write and a workflow transition in one host transaction commit atomically | Done |
| EMB-4 | Flow raises typed errors the host can catch and roll back on; no bare exceptions escape | Done |
| EMB-5 | Flow's tables carry a configurable name prefix to avoid collision with host tables | Done |
| EMB-6 | Flow owns its own migration chain, runnable from the host's migration tool | Done |
| EMB-7 | Schema creation is explicit and idempotent; importing Flow never mutates a database | Done |
| EMB-8 | Actor identity is a typed value passed in-process; Flow never parses credentials | Done |
| EMB-9 | Background routines (timers, jobs, delivery) are callable functions the host schedules | Done |
| EMB-10 | The core is safe to use concurrently from a multi-threaded host process | Done |
| EMB-11 | The core imports only the standard library and its own ports | Done |
| EMB-12 | Embedded and service modes run the same core through the same repository ports | Done |
| EMB-13 | A host may hold a real foreign key from its own tables into Flow's | Done |

## B. Definitions

| ID | Requirement | Status |
|---|---|---|
| DEF-1 | A workflow definition is declarative data, incapable of executing arbitrary code | Done |
| DEF-2 | Published definition versions are immutable | Done |
| DEF-3 | A running instance stays pinned to the version it started on | Done |
| DEF-4 | Publication rejects graphs that are cyclic, unreachable, rootless, or cannot reach an end | Done |
| DEF-5 | Publication rejects unsupported node types, duplicate edges, and edges out of an end node | Untested |
| DEF-6 | Publication rejects a JOIN without a supported rule, a WAIT_SIGNAL without a signal type, and a TIMER without a delay | Untested |
| DEF-7 | Publication validates embedded lifecycle and step FSMs for duplicate and dangling transitions | Untested |
| DEF-8 | Publication rejects unknown guard operators rather than silently evaluating them false | Done |
| DEF-9 | A defined migration policy moves an instance between definition versions | Done |

## C. Execution

| ID | Requirement | Status |
|---|---|---|
| EXE-1 | Workflow lifecycle state, workflow execution status, and node state are separate dimensions | Done |
| EXE-2 | Lifecycle and node transitions are resolved from the pinned FSM version | Done |
| EXE-3 | A transition may require a permission, and is refused without it | Done |
| EXE-4 | A transition may require a reason, and is refused without it | Untested |
| EXE-5 | A transition may carry a guard evaluated against workflow facts | Untested |
| EXE-6 | The graph driver activates nodes whose applicable predecessors are satisfied, to a fixed point | Done |
| EXE-7 | Join rules ALL, ANY and N_OF_M are enforced against applicable predecessors | Done |
| EXE-8 | Edge conditions select which predecessors are applicable | Done |
| EXE-9 | A node on a branch that can no longer run is marked skipped, not left pending forever | Done |
| EXE-10 | A failed node drives the workflow to a declared failure outcome per its configured policy | Done |
| EXE-11 | Terminating a workflow cancels its open nodes, timers, jobs and required children | Done |
| EXE-12 | A suspended workflow performs no node activation, timer firing, or job completion | Done |
| EXE-13 | Each FSM state declares its execution category rather than having one inferred from its name | Done |
| EXE-14 | A parent SUBWORKFLOW node completes when its required children satisfy the configured policy | Done |
| EXE-15 | A child failure policy other than fail-the-parent is honored | Done |
| EXE-16 | An authorized actor may override a join with a mandatory recorded reason | Done |

## D. Work and assignment

| ID | Requirement | Status |
|---|---|---|
| WRK-1 | A node may declare candidate users, roles, groups and organizations | Done |
| WRK-2 | Claiming is refused for an actor outside the candidate set | Done |
| WRK-3 | Candidate organization is enforced against the actor's organization | Done |
| WRK-4 | Reassignment preserves prior assignments as history rather than overwriting them | Untested |
| WRK-5 | A node supports repeated attempts with clarification and response cycles | Untested |
| WRK-6 | Work is queryable by assignee, candidate, state and business key | Done |
| WRK-7 | Work queries are paginated and indexed | Done |
| WRK-8 | A node may carry a due time, and breach raises a configured action | Done |

## E. Facts, signals and external events

| ID | Requirement | Status |
|---|---|---|
| EVT-1 | Fact changes record previous value, new value, source, actor and revision | Done |
| EVT-2 | Guards read facts through a constrained rule language with no code execution | Done |
| EVT-3 | The rule language supports equality, membership, existence and truthiness, composed with all/any/not | Done |
| EVT-4 | The rule language supports numeric comparison and nested field paths | Done |
| EVT-5 | A signal arriving before its waiting node is stored and consumed when the node activates | Done |
| EVT-6 | A signal is consumed exactly once | Done |
| EVT-7 | A repeated signal command causes no second effect | Done |
| EVT-8 | A provider event is deduplicated on connector name and provider event id | Done |
| EVT-9 | An accepted provider event is translated into a signal through the idempotent command path | Done |

## F. Automation and timers

| ID | Requirement | Status |
|---|---|---|
| JOB-1 | An automated node enqueues a durable job rather than executing logic inline | Done |
| JOB-2 | A worker claims a job under a lease; an expired lease permits reclaim | Done |
| JOB-3 | Two workers cannot hold the same job simultaneously | Done |
| JOB-4 | A job retries with backoff to a bounded attempt count, then fails terminally | Untested |
| JOB-5 | A timer is durable and survives process restart | Untested |
| JOB-6 | A timer fires at most once per node iteration | Untested |
| JOB-7 | Timer due times may respect a business calendar | Done |

## G. Reliability

| ID | Requirement | Status |
|---|---|---|
| REL-1 | A repeated command identifier causes no second effect and returns current state | Done |
| REL-2 | Idempotency is checked before revision validation, so a retry never returns a conflict | Done |
| REL-3 | A mutation whose expected revision does not match current is refused with no partial write | Done |
| REL-4 | Every runtime change writes an ordered workflow event in the same transaction | Done |
| REL-5 | Every workflow event writes an outbox record in the same transaction | Done |
| REL-6 | Outbox delivery retries with backoff and moves to dead letter when exhausted | Untested |
| REL-7 | Retry redelivers only to subscriptions that have not yet succeeded | Done |
| REL-8 | Outbound messages carry a schema version | Done |
| REL-9 | A stale worker claim is recovered | Untested |
| REL-10 | Restarting a process alters no workflow or node state | Done |

## H. Operations

| ID | Requirement | Status |
|---|---|---|
| OPS-1 | A workflow's full execution history is retrievable in order | Done |
| OPS-2 | A workflow stuck with no active, waiting or pending work is detectable | Done |
| OPS-3 | Authorized repair actions (retry, skip, force-complete, reassign) exist and are audited | Done |
| OPS-4 | Dead-lettered events can be inspected and redriven | Done |
| OPS-5 | Delivery secrets are never returned by a read interface | Done |
| OPS-6 | Counters for queue depth, timer lag, delivery lag and dead letters are exposed | Done |
| OPS-7 | Command results are stored without embedding a full workflow snapshot | Done |

## I. Non-functional

| ID | Requirement | Status |
|---|---|---|
| NFR-1 | The definition format contains no Python-specific semantics | Done |
| NFR-2 | Repository behavior is defined by a contract suite every adapter must pass | Done |
| NFR-3 | The same contract suite runs against SQLite and the production database in CI | Blocked |
| NFR-4 | A host test suite runs Flow with no container, network or background process | Done |
| NFR-5 | No interface accepts self-asserted actor permissions | Done |
| NFR-6 | Runtime queries used on a request path are indexed and bounded | Done |
| NFR-7 | The graph driver terminates or raises rather than looping unbounded | Untested |
| NFR-8 | Timestamps use one representation throughout | Done |




## Status summary

~~~text
Done        73
Untested    13
Partial      0
Defect       0
Planned      0
Blocked      1
total       87
~~~

73 of 87 requirements are backed by a test: 24 at the first audit, 34 after
the P0 fixes, 42 after the embedding spike, 73 now. The suite is 131 tests,
up from 22.

The 13 rows still marked Untested are implemented but uncited: FSM guard and
reason enforcement, retry backoff, timer restart survival, assignment history
and the driver's iteration bound. They are the next thing worth closing.

## Verified defects

All eleven were reproduced, written as failing tests in
`tests/test_p0_defects.py`, and fixed. None remain open.

`OPS-7` and `NFR-5` were the last two, and each needed a decision rather than a
repair. Both decisions are recorded in [the charter](charter.md) and can be
reversed there:

- **OPS-7.** A repeated command now returns the workflow's *current* state
  rather than a frozen copy of the original response, and storage holds a
  receipt of `{workflow, action, revision}` instead of a full snapshot. The
  alternative — keeping the original response verbatim — was rejected because
  command storage then grows with the square of the number of commands, and a
  caller retrying after a timeout usually wants to know where things stand now.
- **NFR-5.** The HTTP shell takes identity from a trusted header that a gateway
  injects, and the request model can no longer even express a permission. With
  no gateway configured the caller is anonymous and holds nothing, so a
  misconfigured deployment fails closed. Embedded, the question does not arise.

## Open

`NFR-3` is the only requirement that cannot be closed from here. The adapter
contract suite exists (`tests/contract.py`) and SQLite passes it, but there is
no Oracle adapter and no pipeline pointing at one. Certifying Oracle means
subclassing `RepositoryContract` and running it against a real instance.

## Traceability

~~~text
Requirement ID
    -> BDD scenario tagged with the ID
         -> test that asserts it
              -> status column in this document
~~~

A requirement without a citing test may not be marked `Done`.
