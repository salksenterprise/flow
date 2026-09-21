# Workflow Engine Visual Guide

This guide explains Flow using ASCII diagrams. It is intentionally domain-neutral.

## 1. Compact mental model

~~~text
WORKFLOW LIFECYCLE FSM
    Where is the business process overall?

WORKFLOW EXECUTION STATUS
    Is orchestration running, suspended, or finished?

WORKFLOW DAG
    Which activities may run, and in what order?

NODE FSM
    What is happening inside one activity?
~~~

In one picture:

~~~text
+----------------------------------------------------------+
| WORKFLOW INSTANCE                                        |
|                                                          |
| Lifecycle FSM                                            |
|   Stable business-facing state                           |
|                                                          |
| Execution DAG                                            |
|   +----------+      +----------+      +----------+       |
|   | Node A   | ---> | Node B   | ---> | Node C   |       |
|   +----------+      +----------+      +----------+       |
|                           |                              |
|                           v                              |
|                     Node-level FSM                       |
|                     Assignment -> Work -> Completion     |
+----------------------------------------------------------+
~~~

## 2. Definition versus runtime

~~~text
DEFINITION TIME                         RUNTIME

Workflow definition                    Workflow instance
    |                                      |
    +-- version 1                          +-- pinned to version 2
    +-- version 2  ----------------------> +-- lifecycle state
                                           +-- execution status
FSM definition                             +-- node instances
    |                                      +-- events
    +-- version 1                          +-- commands
    +-- version 2  ----------------------> +-- timers/jobs/signals
~~~

Published versions do not change. A new version affects only new workflow instances unless a separately governed migration is performed.

## 3. Three kinds of state

~~~text
Example workflow

Lifecycle state:
    FULFILLING

Workflow execution status:
    RUNNING

Node states:
    Reserve inventory      COMPLETED
    Pack order             IN_PROGRESS
    Create shipment        NOT_READY
    Wait for delivery      NOT_READY
~~~

Changing an inner node normally does not rewrite the workflow lifecycle state.

## 4. Configurable lifecycle FSM

~~~text
                submit
    +-------+ -----------> +-----------+
    | DRAFT |              | SUBMITTED |
    +-------+              +-----+-----+
                                 |
                                 | start
                                 v
                           +-------------+
                           | IN_PROGRESS |
                           +------+------+
                                  |
                                  | complete
                                  v
                             +---------+
                             |  CLOSED |
                             +---------+
~~~

This diagram is only an example. A different process can publish:

~~~text
RECEIVED -> FULFILLING -> SHIPPED -> DELIVERED
~~~

without changing workflow-core.

## 5. Node-level FSM

Default human work:

~~~text
NOT_READY
    |
    | engine activates
    v
  READY
    |
    +---- assign/claim ----> ASSIGNED
    |                          |
    |                          | start
    |                          v
    +------ start --------> IN_PROGRESS
                               |
                +--------------+----------------+
                |              |                |
                | clarify      | wait           | complete
                v              v                v
       CLARIFICATION       WAITING          COMPLETED
          REQUIRED             |                
                |              | resume
                | respond      |
                v              |
       RESPONSE_RECEIVED ------+
                |
                | resume
                v
           IN_PROGRESS
~~~

A template may bind another FSM to a node.

## 6. Basic DAG

~~~text
+---------+     +-----------+     +-----------+     +-----+
| Receive | --> | Transform | --> | Dispatch  | --> | End |
+---------+     +-----------+     +-----------+     +-----+
~~~

The DAG controls dependency. The node FSM controls work inside each box.

## 7. Conditional path

~~~text
                         +-------------------+
                         | Determine route   |
                         +---------+---------+
                                   |
                     +-------------+-------------+
                     |                           |
            expedited = true            expedited = false
                     |                           |
                     v                           v
             +---------------+           +---------------+
             | Express path  |           | Standard path |
             +-------+-------+           +-------+-------+
                     |                           |
                     +-------------+-------------+
                                   |
                                   v
                                +-----+
                                | End |
                                +-----+
~~~

Conditions read controlled workflow facts.

## 8. Parallel fork and ALL join

~~~text
                         +------+
                         | Fork |
                         +--+---+
                            |
              +-------------+-------------+
              |                           |
              v                           v
       +--------------+            +--------------+
       | Activity A   |            | Activity B   |
       +------+-------+            +------+-------+
              |                           |
              +-------------+-------------+
                            |
                            v
                        +--------+
                        |  ALL   |
                        |  Join  |
                        +---+----+
                            |
                            v
                          Next
~~~

The join waits until every applicable predecessor is satisfied.

## 9. ANY and N_OF_M joins

~~~text
Branch A: COMPLETED ----+
                       |
Branch B: IN_PROGRESS --+--> ANY join is satisfied
                       |
Branch C: READY --------+
~~~

For N_OF_M:

~~~text
required_count = 2

Branch A: COMPLETED
Branch B: COMPLETED     ---> join satisfied
Branch C: IN_PROGRESS
~~~

Remaining-branch business policy should be explicit. Completion of an ANY join does not inherently mean unfinished work should be cancelled.

## 10. Parent-child workflows

~~~text
+------------------------------------------------------+
| PARENT WORKFLOW                                      |
|                                                      |
|  +---------+     +----------------+     +---------+  |
|  | Intake  | --> | SUBWORKFLOW    | --> | Finish  |  |
|  +---------+     | node waits     |     +---------+  |
|                  +-------+--------+                  |
|                          |                           |
+--------------------------+---------------------------+
                           |
              +------------+-------------+
              |                          |
              v                          v
     +------------------+       +------------------+
     | Required child A |       | Required child B |
     | Own FSM and DAG  |       | Own FSM and DAG  |
     +------------------+       +------------------+
~~~

The parent node completes when awaited children satisfy policy.

## 11. Required and optional children

~~~text
SUBWORKFLOW node
    |
    +-- Child A   required   COMPLETED
    +-- Child B   required   RUNNING
    +-- Child C   optional   RUNNING

Node result:
    WAITING because required Child B is not complete
~~~

Optional children remain visible but do not block a required-child join when required children exist.

## 12. Explicit child creation

~~~text
Client service
    |
    | POST /workflows/{parent}/children
    v
Flow
    |
    +-- validates parent and SUBWORKFLOW node
    +-- creates child with parent/root references
    +-- emits CHILD_WORKFLOW_STARTED
    +-- waits for child completion
~~~

The client decides which business child is needed. Flow coordinates it.

## 13. Controlled facts

~~~text
Client command
    |
    | facts:
    |   expedited = true
    |   destination_type = INTERNATIONAL
    v
+----------------------------+
| Workflow fact snapshot     |
|                            |
| expedited: true            |
| destination_type: INTL     |
+-------------+--------------+
              |
              v
       DAG guard evaluation
              |
              v
       Activate matching path
~~~

History:

~~~text
fact_key         previous       new       source       revision
expedited        false          true      ORDER_API    7
~~~

## 14. Signal that arrives while waiting

~~~text
WAIT_SIGNAL node
    |
    | signal_type = PACKAGE_COLLECTED
    v
WAITING
    |
    | external signal arrives
    v
Consume signal once
    |
    v
COMPLETED
    |
    v
Downstream node activates
~~~

## 15. Early signal

~~~text
Time ---------------------------------------------------->

Signal arrives                    WAIT_SIGNAL activates
     |                                      |
     v                                      v
Stored unconsumed                  Finds stored signal
SIGNAL_RECEIPT                     Consumes and completes
~~~

The event is not lost because the receiver was not active yet.

## 16. Connector inbox

~~~text
External provider
    |
    | provider_event_id
    v
+-----------------------+
| INBOX_EVENT           |
| unique connector+id   |
+-----------+-----------+
            |
            v
Translate to idempotent signal
            |
            v
Workflow consumes signal
~~~

Duplicate provider delivery returns the prior effect.

## 17. Human assignment across organizations

~~~text
Human work node
    |
    +-- Candidate group: OPERATIONS
    |      Organization: ORG-A
    |
    +-- Candidate group: OPERATIONS
           Organization: ORG-B

Actor:
    user = bob
    group = OPERATIONS
    organization = ORG-B

Result:
    eligible to claim
~~~

An ORG-A actor with the same group is not eligible for the ORG-B candidate.

## 18. Assignment history

~~~text
ROLE: OPERATIONS
    status = REPLACED
          |
          | assigned to Alice
          v
USER: Alice
    status = REPLACED
          |
          | reassigned with reason
          v
USER: Bob
    status = OPEN
~~~

Prior assignments are retained rather than overwritten.

## 19. Clarification loop

~~~text
Worker                       Requestor / external actor
  |                                    |
  | request clarification              |
  +-----------------------------------> |
  |                                    |
  |          response signal/action    |
  | <----------------------------------+
  |                                    |
  v                                    |
Resume work                             |
  |                                    |
  +-- clarify again ------------------> |
  |                                    |
  +-- complete                          |
~~~

Business messages and attachments stay in the client domain. Flow stores execution state and opaque references.

## 20. Work iteration

~~~text
STEP INSTANCE
    |
    +-- Attempt 1
    |     result = rejected/rework
    |
    +-- Attempt 2
          result = completed
~~~

The step definition remains the same; runtime history distinguishes attempts.

## 21. Automation job

~~~text
Workflow API                 Automation worker
     |                              |
     | create QUEUED job            |
     |                              |
     | <------ claim with lease -----+
     |                              |
     |       execute handler         |
     |                              |
     | <------ report result --------+
     |                              |
     v                              |
Complete or retry node              |
~~~

Failure path:

~~~text
RUNNING
   |
   +-- retryable failure --> RETRY_WAIT --> RUNNING
   |
   +-- attempts exhausted -> FAILED
~~~

## 22. Durable timer

~~~text
TIMER node activated
        |
        v
Timer stored in database
        |
        | process restarts safely
        |
        v
due_at reached
        |
        v
Worker fires timer once
        |
        v
Node completes
~~~

## 23. Workflow suspension

~~~text
RUNNING
   |
   | suspend
   v
SUSPENDED
   |
   | no DAG driving or step action
   |
   | resume
   v
RUNNING
~~~

Suspension changes execution status. It does not have to change the business lifecycle state.

## 24. Authorized join override

~~~text
Branch A: COMPLETED
Branch B: BLOCKED
        |
        v
JOIN: NOT_READY
        |
        | actor has workflow.override
        | mandatory reason supplied
        v
JOIN_OVERRIDDEN event
        |
        v
Downstream execution continues
~~~

The override is exceptional, explicit, and auditable.

## 25. Command idempotency

~~~text
Command ID 123
    |
    v
Execute and commit result
    |
    X response lost

Client retries Command ID 123
    |
    v
Return stored result
No second transition
~~~

## 26. Optimistic locking

~~~text
Client A reads revision 5
Client B reads revision 5

Client A submits expected_revision = 5
    -> succeeds, revision becomes 6

Client B submits expected_revision = 5
    -> conflict
    -> must reload before deciding what to do
~~~

## 27. Transactional outbox

~~~text
BEGIN
  update workflow/node
  append WORKFLOW_EVENT
  insert OUTBOX_EVENT
  record command result
COMMIT
~~~

Delivery happens after commit:

~~~text
OUTBOX_EVENT
    |
    v
Claim
    |
    +-- deliver ------> DELIVERED
    |
    +-- fail ---------> PENDING with backoff
                           |
                           +-- attempts exhausted
                                   |
                                   v
                              DEAD_LETTER
~~~

## 28. One moment in a running workflow

~~~text
WORKFLOW WF-250
|
| Lifecycle: FULFILLING
| Execution: RUNNING
| Revision: 18
|
+-- Reserve inventory
|      execution = COMPLETED
|
+-- Pack goods
|      state = IN_PROGRESS
|      assignee = warehouse-17
|      organization = EAST_DC
|
+-- Create shipment
|      execution = NOT_READY
|
+-- Wait for carrier
|      execution = NOT_READY
|
+-- Automation jobs
|      none active
|
+-- Timers
       packing SLA due tomorrow
~~~

## 29. Database relationship view

~~~text
FSM_DEFINITION
    +----< FSM_VERSION
              +----< FSM_STATE_DEFINITION
              +----< FSM_TRANSITION_DEFINITION

WORKFLOW_DEFINITION
    +----< WORKFLOW_VERSION
              +----< STEP_DEFINITION
              +----< TRANSITION_DEFINITION

WORKFLOW_INSTANCE
    +----< child WORKFLOW_INSTANCE
    +----< STEP_INSTANCE
    |         +----< STEP_ATTEMPT
    |         +----< WORK_CANDIDATE
    |         +----< WORK_ASSIGNMENT
    |         +----< AUTOMATION_JOB
    |         +----< DURABLE_TIMER
    |
    +----< SIGNAL_RECEIPT
    +----< WORKFLOW_EVENT
    +----< WORKFLOW_COMMAND
    +----< WORKFLOW_FACT_HISTORY
~~~

## 30. Final mental model

~~~text
Definitions answer:
    What process is allowed?

Lifecycle FSM answers:
    Where is the process overall?

DAG answers:
    What can run next?

Node FSM answers:
    What is happening inside this unit of work?

Facts answer:
    Which configured path applies?

Signals answer:
    What happened outside Flow?

Child workflows answer:
    Which independently managed processes must finish?

Inbox/outbox answer:
    How are external events delivered reliably?
~~~
