# ISRP Orchestration

Status: Current. This is the implemented orchestration baseline and the
authority for execution behavior.

ISRP owns its own orchestration. There is no separate workflow engine, generic
core, or second product to keep in step. The state machines, dependency graph,
durable timers, and reliability plumbing are ISRP code in ISRP's module tree,
using ISRP's types. A repository port remains solely because SQLite and Oracle
must implement the same persistence behavior.

This section replaces the Flow charter, requirements, technical design and
engine reference. Those four documents described a domain-neutral product with
one consumer, and the cost of keeping them true to each other, and to ISRP's own
design, produced most of the defects found during review.

## Why not a generic engine

The generic framing was reasonable and it was tried. What it bought was a clean
core that could be tested without a database. What it cost was a boundary to
maintain, and with exactly one consumer that trade did not pay:

~~~text
entity names in the ISRP model that the engine did not have
capabilities the ISRP design assumed and the engine lacked
two outboxes, two inboxes and two audit trails in one database
a step FSM defined differently in two documents
2.5 lines of specification per line of implementation
~~~

Each of those was boundary maintenance, not a problem with the process being
automated. Fusing removes the category.

The option that is given up is serving a second process later without extraction
work. If one appears, the orchestration modules are extractable — they are a
coherent subsystem, not scattered through the domain — but nothing is built now
to make that easy, and no claim of generality is made.

# Part I: What the fusion collapses

The single largest simplification is that a workflow instance stops being a
separate record. An ISRP request *is* a workflow instance; so is an assessment.
They already carry a lifecycle status, a revision and a pinned definition
version.

| Removed | Replaced by |
|---|---|
| `workflow_instance` | `isrp_request` and `isrp_assessment` carry `lifecycle_status`, `execution_status`, `revision` directly |
| `business_type`, `business_key`, `correlation_id` as opaque references | real foreign keys, enforced by the database |
| `workflow_subject` | `request_subject` and `assessment_subject`, which already exist |
| parent and child workflow machinery | the request-to-assessment relationship, which already exists |
| `workflow_version` pinning as a separate concern | `isrp_request.workflow_version_id` and `isrp_assessment.workflow_version_id` |
| the *generic engine* boundary and everything that served it | one codebase; the *database* boundary stays, because Oracle needs it |
| actor type juggling for string, mapping and object forms | ISRP's authenticated actor, one type |
| receipts holding a serialized copy of the response | `{owner_type, owner_id, action, revision, request_fingerprint}` in columns; `ISRP_COMMAND` covers commands that never reach orchestration |
| `WORKFLOW` as a reserved aggregate type in the event log | ISRP aggregate types only |
| HTTP service shell, webhook subscriptions, operator console | ISRP's own API, its own consumers, its own screens |

Roughly a third of the orchestration code is that indirection. It goes.

## What survives, because it is load-bearing

These are the parts that were hard to get right, and they are kept as they are:

~~~text
the graph driver and its fixed-point loop
step state machines with declared execution categories
join rules, edge conditions and dead-branch skipping
the guard language, evaluated against routing facts
durable timers, automation jobs and signals
service levels with business-calendar deadlines
the shared event log, outbox and inbox
optimistic revisions and command idempotency
publication-time validation
~~~

Every one of these has tests today. They move; they are not rewritten.

# Part II: State

Three dimensions, unchanged in substance, now carried on ISRP's own rows.

~~~text
ISRP_REQUEST / ISRP_ASSESSMENT
    lifecycle_status      business-facing, from the pinned lifecycle FSM
    execution_status      RUNNING SUSPENDED COMPLETED FAILED CANCELLED
    revision              optimistic lock

STEP_INSTANCE
    state                 from the pinned step FSM
    execution_status      the engine's category for that state
~~~

A request's `lifecycle_status` of `ASSESSMENTS_IN_PROGRESS` says where the review
is. Its `execution_status` says whether orchestration is live. A step's
`IN_REVIEW` says what is happening inside one activity. Collapsing these
produces the single enormous state machine that makes jBPM processes hard to
follow, which is the thing this design exists to avoid.

`STEP_INSTANCE` gains a polymorphic owner, because a step may belong to a
request's orchestration graph or to an assessment's execution graph:

~~~text
step_instance
    owner_type            ISRP_REQUEST | ISRP_ASSESSMENT
    owner_id
    step_definition_id
    iteration_number
    state
    execution_status
    result_json
    activated_at started_at completed_at
~~~

Constraint: `UNIQUE(owner_type, owner_id, step_definition_id)`.

## Execution categories

A step FSM state declares the category the engine treats it as. This is what
lets ISRP use its own vocabulary without the engine recognising any of the names.

~~~text
NOT_READY  READY  ACTIVE  WAITING  COMPLETED  SKIPPED  FAILED  CANCELLED
~~~

The human-review FSM, defined once in [Architecture](architecture.md), maps onto
these. `BLOCKED` is a step state whose category is `WAITING`; attention
projections derive the `BLOCKED` label from ISRP's own records.

# Part III: Definitions

A workflow definition is data. It cannot execute code. Published versions are
immutable and a running request or assessment stays pinned to the version it
started on.

~~~text
workflow_definition        workflow_version
step_definition            transition_definition
fsm_definition             fsm_version
fsm_state_definition       fsm_transition_definition
~~~

These keep their names. They are definition tables, not runtime tables, and no
ISRP concept wants those names.

Publication validates and refuses:

~~~text
a graph that is cyclic, rootless, unreachable, or cannot reach an END
an unsupported node type, a duplicate edge, an edge out of an END
a JOIN without a rule, a WAIT_SIGNAL without a signal type,
    a TIMER without a delay
an FSM with duplicate or dangling transitions
a state declaring an unknown execution category
an unknown failure policy, child failure policy or breach action
a malformed business calendar
a guard naming an operator that is not implemented
an execution mode outside HUMAN and AUTOMATION
~~~

The last line is new and is only possible once fused. Execution mode is an ISRP
concept; a generic engine had no field for it, so the delivery plan's
requirement to reject AI modes at publication could not be enforced. Now it can.

## The guard language

~~~text
eq  ne  in  not_in  exists  truthy
gt  gte  lt  lte  contains  starts_with
composed with all, any, not
~~~

Fields are dotted paths, so `subject.classification` reads a nested routing
fact. A missing path is distinguishable from a null value. Every operator is
total: a type mismatch is false, never an error, because a guard runs
mid-transaction and must not be able to crash a review.

# Part IV: Execution

## The driver

Runs to a fixed point, bounded, raising rather than hanging if a definition
oscillates.

~~~text
repeat until nothing changes, or 200 times:

  reload the owner; stop unless execution_status is RUNNING

  for each NOT_READY step:
      can activate?        -> activate
      otherwise dead?      -> skip

  for each READY step, by type:
      FORK JOIN MILESTONE  -> complete immediately
      END                  -> complete the owner, then drive its parent
      WAIT_SIGNAL          -> wait, consuming a signal already stored
      AUTOMATED_TASK       -> enqueue a job, wait
      TIMER                -> schedule a durable timer, wait
      ASSESSMENT           -> wait for the assessments it owns

  for each WAITING step:
      WAIT_SIGNAL          -> consume a matching signal if one arrived
      AUTOMATED_TASK       -> complete if its job has since succeeded
      ASSESSMENT           -> complete or fail on the configured policy
~~~

The `SUBWORKFLOW` node type is renamed `ASSESSMENT`, because that is the only
thing it ever waits for. Its configuration loses `business_type`,
`business_key`, `relationship_type` and `relationship_key`; an assessment's
identity is its own row. What is left is the version to run, the type of
assessment and whether it is required, and the node opens the assessment itself
when it activates. An `ASSESSMENT` node inside an assessment is refused: there
are two levels, so a third has nowhere to go.

The `WAITING` pass is what lets execution recover rather than stall. A job that
finished while a request was on hold is reconciled there when it resumes.

Every command runs the driver before committing, so a request is always observed
at a fixed point, never mid-cascade.

## Joins and routing

| Rule | Satisfied when |
|---|---|
| `ALL` | every applicable predecessor is satisfied |
| `ANY` | at least one applicable predecessor is satisfied |
| `N_OF_M` | at least `required_count` are satisfied, which covers a fixed quorum |
| `ALL_REQUIRED` | every predecessor is satisfied, applicable or not |

A predecessor is satisfied when its execution status is `COMPLETED` or
`SKIPPED`, or `FAILED` under a `CONTINUE` failure policy.

Quorum that depends on business rules, and decision-authority progression, are
`WAIT_SIGNAL` nodes that ISRP feeds when its own rules are satisfied. They are
not join rules and never were.

A step whose predecessors have all reached a terminal status and which still
cannot activate is marked `SKIPPED`. Without that, a step on an untaken branch
stays pending for the life of the review and every progress count derived from
it is wrong.

## Failure

| `on_failure` | Effect |
|---|---|
| `FAIL_WORKFLOW` (default) | the request or assessment goes to `FAILED` |
| `SUSPEND` | it goes to `SUSPENDED` for intervention |
| `CONTINUE` | the step stays failed; the graph proceeds around it |

An automation step that exhausts its attempts under `SUSPEND` is the
`MANUAL_REVIEW_NEEDED` outcome the phase model calls for.

# Part V: Work

Candidates, assignment, claiming and organization-aware eligibility are
unchanged. Reassignment marks the prior assignment `REPLACED` rather than
overwriting it.

A step may carry `due_in_seconds`. On activation a durable timer is scheduled
with action `SLA_BREACH`; on breach the event is always recorded and the
configured action follows:

| `on_breach` | Effect |
|---|---|
| `NOTIFY` (default) | event only |
| `ESCALATE` | reassign to `escalate_to`, recorded |
| `FAIL` | the step fails, and its failure policy applies |

Deadlines may respect a business calendar, so four working hours from Friday
afternoon land on Monday morning rather than over the weekend.

Work is queryable by assignee, by candidate eligibility, by state and by
request or assessment, paginated, and indexed for both.

# Part VI: Reliability

One event log, one outbox, one inbox, already decided and already built. Fusing
removes the last special case: there is no `WORKFLOW` aggregate type, because
there is no workflow aggregate. Orchestration events are recorded against the
request or assessment they belong to.

~~~text
event_log
  aggregate_type    ISRP_REQUEST | ISRP_ASSESSMENT | ISRP_FINDING | ...
  aggregate_id
  sequence_number   dense per aggregate
  event_type
  ...
~~~

An orchestration event and a domain event about the same assessment now share
one sequence, which they could not while the engine owned a separate instance.
A consumer reading `ISRP_ASSESSMENT / ASMT-1002` sees the reviewer's
determination and the step transition it caused, in order, in one stream. That
was impossible before and is the clearest single argument for fusing.

Every runtime change writes an event and an outbox row in the same transaction.
Every envelope carries a schema version. Delivery retries only to subscriptions
that have not already accepted. Jobs and inbox receipts are leased to a named
worker; a stale worker cannot complete work after another worker reclaims it.
Provider events are deduplicated on connector and provider event id, then
translated into signals through the same idempotent command path.

Commands carry a client-generated identifier bound to the operation, target,
and semantic request payload. An exact repeat has one effect and returns current
state; conflicting reuse is rejected. Mutations validate `expected_revision`
and refuse a mismatch with no partial write.

# Part VII: Testing without a boundary

The one real cost of fusing is that orchestration can no longer be tested in
isolation from the domain. That cost is worth naming and containing, because it
is how the defects found during review were found in the first place.

Two rules keep it contained, and they are module hygiene rather than a contract
to negotiate:

1. The orchestration modules do not import ISRP domain models. They take
   identifiers, statuses and configuration, not assessments and findings. This
   is not a purity doctrine; it is what keeps a driver test from needing an
   evidence fixture.
2. Orchestration tests build their graphs from fixtures declared in the test,
   not from published ISRP templates. A join-rule test should fail because the
   join rule is wrong, never because a requirement catalog changed.

The existing orchestration tests transfer under those rules. They are the
asset that makes this change safe to attempt at all.

# Part VIII: Migration from the current code

The engine works and is tested. This is a restructuring, not a rewrite, and it
should be done in an order where the tests keep passing.

~~~text
1  DONE. deleted the HTTP shell, the worker process and the console,
   with the Dockerfile and compose file that only existed to deploy them

2  DONE. moved into isrp/orchestration/; the ISR template into isrp/templates/
   and the reference host into tests/, where it is really a fixture

3  DONE. kept the repository port; it is not part of what fusing removes
   see "Two boundaries, only one removed" below

4  DONE. folded workflow_instance into isrp_request and isrp_assessment
   every runtime row now names its owner as (owner_type, owner_id);
   the engine entry points are start_request and start_assessment

5  DONE. deleted the opaque reference fields and the parent-child machinery
   isrp_assessment.request_id is the whole of the relationship

6  DONE. renamed SUBWORKFLOW to ASSESSMENT and dropped its reference
   configuration; the node opens its assessment itself

7  DONE. execution mode on step definitions; publication rejects AI modes
   HUMAN and AUTOMATION are current; the two AI modes are named so that
   publication can refuse them by name. Engine-driven nodes carry none.

8  DONE. deleted the WORKFLOW aggregate type from the event log
   it fell out of step 4: an execution event is written against the
   aggregate it drove, so there is nothing left to reserve
~~~

Steps 4, 5, 6 and 8 were one change, because they are one change: an owner
type cannot be introduced without deciding what the second owner is, and the
answer to that deletes the parent-child machinery, the opaque references and
the reserved aggregate type in the same motion. Version 7 of the schema carries
an existing database across: a run with no parent becomes a request, a run with
a parent becomes an assessment of the request at the root of its tree, and ids
carry over unchanged because they came from one sequence and stay unique across
the two tables. A tree deeper than two levels is flattened onto that root and
loses its parent step pointer; that is the only lossy part, and it is asserted
in `tests/test_owner_migration.py` rather than left to be discovered.

## Two boundaries, only one removed

An earlier draft of this plan proposed deleting the repository port along with
the rest of the generic-engine apparatus. That was wrong, and it is worth
writing down why, because the two abstractions look alike and only one is
speculative.

~~~text
the generic engine boundary     ISRP <-> a domain-neutral workflow product
                                one consumer, never a second
                                REMOVED

the database boundary           orchestration <-> SQLite or Oracle
                                two implementations, both required
                                KEPT
~~~

SQLite is the development and test adapter and Oracle is the production store.
That is decision 8 in the [README](README.md), and iteration 12 of the
[delivery plan](delivery-plan.md) requires Oracle to pass the same repository
contract suite as SQLite before production use. Deleting the port would make
that impossible and would mean writing the Oracle store twice over with nothing
holding the two to the same behaviour.

So the port and the contract suite stay. What changes is only how they are
described: they are a database-portability device, not a claim that the
orchestration is reusable by anyone else.

Steps 1 and 2 are mechanical and keep every test green. Step 4 is the one that
needs care: it changes the runtime schema, and the existing migration chain
should carry it rather than a fresh start.

# Part IX: Documentation lineage

The former Flow charter, requirements, technical design, integration contract,
and engine-reference directory have been removed. Their current, ISRP-specific
content is consolidated into this design set; Git history preserves the
superseded text without leaving two architectures in the documentation tree.

[Workflow Engine Releases 1-3](../workflow-engine-releases-1-3.md) remains as
an explicitly historical delivery record. The
[definition of done](../definition-of-done.md) remains current because its
quality gates came from real defects and do not depend on orchestration being a
separate product.
