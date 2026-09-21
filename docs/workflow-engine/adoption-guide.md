# Adopting Flow for Another Business Process

## 1. Purpose

Flow is not limited to reviews, assessments, approvals, or security processes. It can coordinate any long-running process that has:

- a stable business identity
- an explicit lifecycle
- ordered or parallel activities
- human or automated work
- external events
- deadlines or waiting periods
- retry and recovery requirements

This guide shows how to adopt Flow for a process with a different shape: order fulfillment.

## 2. First decide whether Flow is appropriate

Flow is a good fit when the process:

- lasts longer than one database transaction
- crosses services, teams, or organizations
- waits for humans or external events
- needs parallel work and joins
- needs a durable audit trail
- must resume after failure
- has meaningful cancellation, suspension, or retry behavior
- benefits from versioned process definitions

Flow may be unnecessary when:

- one synchronous API call can complete the operation safely
- the work is a pure batch transformation
- there is no durable business identity
- all work belongs inside one database transaction
- the need is only message transport
- the process is primarily unstructured collaboration with no stable execution rules

Decision guide:

~~~text
Does work span time, systems, or people?
    |
    +-- No --> Keep it inside the domain service
    |
    +-- Yes
          |
          v
Does it need durable state, retry, or audit?
    |
    +-- No --> Event/message choreography may be sufficient
    |
    +-- Yes
          |
          v
Does it have a repeatable lifecycle or dependency graph?
    |
    +-- No --> Case-management tooling may fit better
    |
    +-- Yes --> Flow is a candidate
~~~

## 3. Preserve the ownership boundary

The adopting service remains the system of record.

For order fulfillment:

~~~text
ORDER SERVICE OWNS                    FLOW OWNS

Order and line items                  Workflow instance
Customer and addresses                Lifecycle execution
Prices and taxes                      DAG activation
Payment records                       Human assignments
Inventory reservations                Automation jobs
Packages and shipments                Carrier signals
Refunds and cancellations             Timers
Business audit                        Execution audit
~~~

Flow stores references such as:

~~~text
business_type = ORDER
business_key = ORD-100245
correlation_id = ORD-100245

result_reference = order://shipments/SHP-900
~~~

Do not copy the entire order, payment, or shipment into workflow variables.

## 4. Adoption method

Use the following sequence.

### Step 1: Identify the business aggregate

Choose the record whose lifecycle the process coordinates.

Examples:

~~~text
ORDER
CUSTOMER_ONBOARDING
SERVICE_ACTIVATION
MANUFACTURING_JOB
INCIDENT_RESPONSE
BENEFIT_ENROLLMENT
RETURN
~~~

Define:

- business type
- business key
- source system
- correlation ID
- relevant subjects

### Step 2: Define the business lifecycle

Use a small lifecycle FSM. Avoid putting every work activity into the lifecycle.

Order example:

~~~text
RECEIVED
    |
    | accept
    v
FULFILLING
    |
    | ship
    v
SHIPPED
    |
    | confirm_delivery
    v
DELIVERED
~~~

Exceptional lifecycle states:

~~~text
ON_HOLD
CANCELLED
FAILED
~~~

Lifecycle questions:

- Which states matter to business users?
- Which states may the domain service expose externally?
- Which transitions require authorization?
- Which transitions require a reason?
- Which states are terminal?

### Step 3: Separate lifecycle from activities

Do not create lifecycle states such as:

~~~text
CALLING_PAYMENT_API
WAITING_FOR_WAREHOUSE_WORKER
PRINTING_LABEL
~~~

Those are DAG or node states.

Use:

~~~text
Lifecycle: FULFILLING

Active nodes:
    Reserve inventory
    Authorize payment

Waiting node:
    Carrier collection
~~~

### Step 4: Draw the happy-path DAG

Start with only the normal route.

~~~text
Receive order
      |
      v
Validate order
      |
      v
     Fork
      |
      +----------------------+
      |                      |
      v                      v
Reserve inventory      Authorize payment
      |                      |
      +----------+-----------+
                 |
                 v
               Join
                 |
                 v
             Pick and pack
                 |
                 v
          Create shipment
                 |
                 v
       Wait for carrier pickup
                 |
                 v
         Wait for delivery
                 |
                 v
                End
~~~

### Step 5: Classify every node

| Business activity | Flow node type |
|---|---|
| Receive/validate order | AUTOMATED_TASK |
| Parallel split | FORK |
| Reserve inventory | AUTOMATED_TASK |
| Authorize payment | AUTOMATED_TASK |
| Wait for both | JOIN |
| Pick and pack | HUMAN_TASK |
| Create carrier shipment | AUTOMATED_TASK |
| Wait for pickup | WAIT_SIGNAL |
| Wait for delivery | WAIT_SIGNAL |
| Delivery timeout | TIMER or separate timed path |
| Complex regional fulfillment | SUBWORKFLOW |
| Finish | END |

### Step 6: Define node FSMs

Automation:

~~~text
NOT_READY -> READY -> QUEUED -> RUNNING
                                  |
                                  +-> SUCCEEDED
                                  +-> RETRY_WAIT
                                  +-> FAILED
~~~

Warehouse work:

~~~text
NOT_READY -> READY -> ASSIGNED -> IN_PROGRESS -> COMPLETED
                                      |
                                      +-> WAITING
~~~

External wait:

~~~text
NOT_READY -> READY -> WAITING -> COMPLETED
~~~

Only introduce a custom FSM when the default human-step behavior is insufficient.

### Step 7: Define orchestration facts

Use facts only for routing and execution.

Appropriate:

~~~text
expedited = true
international = false
requires_cold_chain = true
warehouse_code = EAST_DC
split_shipment_required = false
~~~

Inappropriate:

~~~text
entire_order_json
full_customer_profile
credit_card_details
complete_inventory_record
shipping_label_binary
~~~

For large or sensitive data, store an opaque reference.

### Step 8: Define external signals

List events that Flow must wait for.

Order example:

~~~text
PAYMENT_AUTHORIZED
INVENTORY_RESERVED
PACKAGE_PICKED_UP
PACKAGE_DELIVERED
PACKAGE_DELIVERY_FAILED
ORDER_CANCEL_REQUESTED
~~~

For each signal define:

- producing system
- stable provider event ID
- workflow lookup method
- signal type
- correlation key
- payload schema
- duplicate behavior
- late-arrival behavior

### Step 9: Define human assignment

For pick-and-pack:

~~~text
candidate_type = GROUP
candidate_value = WAREHOUSE_OPERATOR
organization_id = EAST_DC
~~~

Decide:

- who may claim work
- who may reassign it
- whether one or several assignments are mandatory
- due date
- escalation target
- separation-of-duty requirements

### Step 10: Define failure policy

Every external or automated node needs an explicit failure decision.

~~~text
Reserve inventory fails
    |
    +-- retryable system error --> retry
    |
    +-- insufficient stock ----> client domain decides:
                                  backorder, alternate warehouse,
                                  partial fulfill, or cancel

Payment fails
    |
    +-- retryable gateway error -> retry
    |
    +-- declined ----------------> wait for domain action or cancel

Carrier API fails
    |
    +-- retry
    +-- manual shipping fallback
~~~

The automation worker reports execution failure. The domain service decides business meaning.

## 5. Order-fulfillment architecture

~~~text
+------------------+                        +------------------+
| Order Service    |                        | Inventory Service|
| System of record |                        +---------+--------+
+--------+---------+                                  |
         |                                            |
         | commands, facts, signals                   |
         v                                            |
+--------------------------+                          |
| Flow                     | <------------------------+
|                          |
| Order lifecycle FSM      | <------------------------+
| Fulfillment DAG          |                          |
| Human warehouse work     |                +---------+--------+
| Jobs, signals, timers    |                | Payment Service  |
+-------------+------------+                +------------------+
              |
              | automation jobs / signals
              v
     +------------------+
     | Carrier Adapter  |
     +------------------+
~~~

## 6. Order-fulfillment sequence

~~~text
Order Service         Flow            Inventory       Payment       Warehouse       Carrier
     |                  |                  |              |              |              |
     | Start workflow   |                  |              |              |              |
     +----------------->|                  |              |              |              |
     |                  | queue reserve    |              |              |              |
     |                  +----------------->|              |              |              |
     |                  | queue authorize  |              |              |              |
     |                  +-------------------------------->|              |              |
     |                  |                  |              |              |              |
     |                  | inventory result |              |              |              |
     |                  |<-----------------+              |              |              |
     |                  | payment result                  |              |              |
     |                  |<--------------------------------+              |              |
     |                  |                                                |              |
     |                  | assign pick/pack                               |              |
     |                  +----------------------------------------------->|              |
     |                  |                                                |              |
     |                  | completed                                      |              |
     |                  |<-----------------------------------------------+              |
     |                  |                                                               |
     |                  | create shipment job                                           |
     |                  +-------------------------------------------------------------->|
     |                  |                                                               |
     |                  | shipment reference                                            |
     |                  |<--------------------------------------------------------------+
     |                  |                                                               |
     |                  | wait PACKAGE_PICKED_UP                                         |
     |                  |<--------------------------------------------------------------+
     |                  |                                                               |
     |                  | wait PACKAGE_DELIVERED                                         |
     |                  |<--------------------------------------------------------------+
     |                  |                                                               |
     | WORKFLOW_COMPLETED|                                                               |
     |<-----------------+                                                               |
~~~

## 7. Example lifecycle FSM definition

~~~json
{
  "key": "order-fulfillment-lifecycle",
  "name": "Order fulfillment lifecycle",
  "version": 1,
  "initial_state": "RECEIVED",
  "states": [
    "RECEIVED",
    "FULFILLING",
    "SHIPPED",
    {"key": "DELIVERED", "terminal": true},
    {"key": "CANCELLED", "terminal": true}
  ],
  "transitions": [
    {"action": "accept", "from": "RECEIVED", "to": "FULFILLING"},
    {"action": "ship", "from": "FULFILLING", "to": "SHIPPED"},
    {"action": "confirm_delivery", "from": "SHIPPED", "to": "DELIVERED"},
    {
      "action": "cancel",
      "from": "RECEIVED",
      "to": "CANCELLED",
      "required_permission": "order.cancel",
      "reason_required": true
    }
  ]
}
~~~

If cancellation is valid from several states, define an explicit transition from each permitted source state.

## 8. Example workflow skeleton

~~~json
{
  "key": "order-fulfillment",
  "name": "Order Fulfillment",
  "domain": "ORDER_MANAGEMENT",
  "version": 1,
  "publish": true,
  "lifecycle_fsm": {"key": "order-fulfillment-lifecycle", "...": "..."},
  "steps": [
    {"key": "validate", "name": "Validate order", "type": "AUTOMATED_TASK",
     "configuration": {"handler": "order.validate"}},
    {"key": "fork", "name": "Start reservation and payment", "type": "FORK"},
    {"key": "inventory", "name": "Reserve inventory", "type": "AUTOMATED_TASK",
     "configuration": {"handler": "inventory.reserve", "max_attempts": 5}},
    {"key": "payment", "name": "Authorize payment", "type": "AUTOMATED_TASK",
     "configuration": {"handler": "payment.authorize", "max_attempts": 3}},
    {"key": "join", "name": "Reservation and payment complete", "type": "JOIN",
     "join_rule": "ALL"},
    {"key": "pack", "name": "Pick and pack", "type": "HUMAN_TASK",
     "configuration": {
       "candidates": [
         {"type": "GROUP", "value": "WAREHOUSE_OPERATOR", "organization_id": "EAST_DC"}
       ]
     }},
    {"key": "ship", "name": "Create shipment", "type": "AUTOMATED_TASK",
     "configuration": {"handler": "carrier.create_shipment"}},
    {"key": "pickup", "name": "Wait for pickup", "type": "WAIT_SIGNAL",
     "configuration": {"signal_type": "PACKAGE_PICKED_UP"}},
    {"key": "delivery", "name": "Wait for delivery", "type": "WAIT_SIGNAL",
     "configuration": {"signal_type": "PACKAGE_DELIVERED"}},
    {"key": "end", "name": "Fulfillment complete", "type": "END"}
  ],
  "transitions": [
    {"from_step": "validate", "to_step": "fork"},
    {"from_step": "fork", "to_step": "inventory"},
    {"from_step": "fork", "to_step": "payment"},
    {"from_step": "inventory", "to_step": "join"},
    {"from_step": "payment", "to_step": "join"},
    {"from_step": "join", "to_step": "pack"},
    {"from_step": "pack", "to_step": "ship"},
    {"from_step": "ship", "to_step": "pickup"},
    {"from_step": "pickup", "to_step": "delivery"},
    {"from_step": "delivery", "to_step": "end"}
  ]
}
~~~

The abbreviated lifecycle object must be replaced by its full definition before import.

## 9. Handling variants without copying templates

Use controlled facts and conditional edges for bounded variants.

~~~text
requires_cold_chain = true
    -> activate cold-chain packaging

international = true
    -> activate customs-document subprocess

expedited = true
    -> activate express carrier path
~~~

Use a child workflow when a variant:

- has its own lifecycle
- is independently owned
- contains many nodes
- is reusable
- may continue after the parent changes state

Example:

~~~text
Order fulfillment parent
    |
    +-- International customs child workflow
~~~

## 10. Cancellation and compensation

Cancellation is not the same as deleting execution history.

~~~text
Cancel requested
      |
      v
Can order still be cancelled?
      |
      +-- No --> reject business command
      |
      +-- Yes
             |
             +-- release inventory
             +-- void payment authorization
             +-- cancel shipment if created
             +-- transition lifecycle to CANCELLED
~~~

Compensation activities are ordinary nodes or child workflows. Flow coordinates them; each domain service owns the compensating business action.

## 11. Timers and service-level objectives

Examples:

~~~text
Payment authorization must finish in 2 minutes
Warehouse work should finish in 4 hours
Carrier pickup expected within 24 hours
Delivery expected within 7 days
~~~

Timer policy must state:

- when the clock starts
- due time calculation
- time zone or business calendar
- action at warning threshold
- action at breach
- whether the process fails, escalates, or continues

The current baseline provides durable timer nodes. Rich business-calendar and escalation actions should be added as configuration rather than embedded in an order-specific engine fork.

## 12. Integration strategies

### Command-driven

The domain service directly starts workflows and sends facts or signals.

Use when:

- synchronous acknowledgement is useful
- the caller can retain command IDs
- direct availability coupling is acceptable

### Event-driven

A connector receives domain events and posts external events into Flow.

Use when:

- the domain already publishes durable events
- loose coupling is preferred
- duplicate delivery is expected

### Hybrid

Start the workflow synchronously, then use events for long-running progress.

This is the usual recommendation.

~~~text
Create order
    -> synchronous workflow start

Inventory/payment/carrier updates
    -> asynchronous events and signals
~~~

## 13. Rollout plan

### Phase 1: Model and simulate

- document lifecycle FSM
- draw happy path
- list exception paths
- identify business/system boundaries
- validate with process owners

### Phase 2: Shadow execution

- start Flow alongside the existing process
- do not let Flow initiate irreversible effects
- compare expected and actual transitions
- capture missing states and events

### Phase 3: Limited automation

- enable deterministic, reversible automation
- keep manual fallback
- monitor retries, timer lag, and dead letters

### Phase 4: Authoritative orchestration

- Flow becomes the execution coordinator
- domain service remains business system of record
- publish operational runbooks
- enable repair and replay procedures

### Phase 5: Optimize

- extract reusable child workflows
- simplify facts
- tune retry and timeout policies
- retire obsolete workflow versions

## 14. Testing strategy

Test at four levels.

### Definition tests

- graph validates
- every path reaches END
- every condition has test coverage
- lifecycle and node transitions are complete

### Engine tests

- fork/join behavior
- parent-child completion
- early and late signals
- duplicate commands
- retry and timer behavior

### Contract tests

- automation handlers
- provider event schemas
- webhook signatures
- opaque reference formats

### Business scenario tests

Order examples:

~~~text
Normal fulfillment
Payment retry then success
Insufficient inventory
Split shipment
Carrier pickup arrives before wait node
Delivery timeout
Cancellation before reservation
Cancellation after shipment rejected
Duplicate carrier event
Worker crash after external effect
~~~

## 15. Operating model

Assign ownership for:

| Concern | Recommended owner |
|---|---|
| Workflow platform | Platform/workflow team |
| Business template | Domain process owner |
| Automation handler | Owning domain/service team |
| Identity and permissions | Enterprise IAM/platform |
| Timer and SLA policy | Business owner plus operations |
| Incident response | Platform and domain teams |
| Definition publication | Governed release process |
| Dead-letter repair | Platform operations |

## 16. Adoption anti-patterns

### Copying the domain database into variables

Why it fails:

- creates stale duplicate state
- increases sensitive-data exposure
- makes reconciliation difficult

Use references and minimal routing facts.

### Putting business logic into workflow-core

Why it fails:

- breaks domain neutrality
- makes releases coupled
- causes one client's policy to affect others

Put business logic in the domain service or automation handler.

### Modeling every activity as a lifecycle state

Why it fails:

- creates an enormous FSM
- hides parallel work
- makes status unstable

Use the lifecycle FSM for coarse business state and the DAG for activities.

### Using webhooks as exactly-once delivery

Why it fails:

- network outcomes are ambiguous
- providers retry
- consumers may process before losing a response

Use event IDs and idempotent consumers.

### Editing published definitions

Why it fails:

- running history becomes uninterpretable
- old instances change behavior unexpectedly

Publish a new version.

### Creating a child workflow for every small task

Why it fails:

- increases operational and query complexity
- obscures the parent path

Use child workflows only for independently meaningful subprocesses.

## 17. Adoption checklist

Business model:

- [ ] Business aggregate and key identified
- [ ] System of record identified
- [ ] Lifecycle states and transitions agreed
- [ ] Terminal and exceptional outcomes agreed

Execution:

- [ ] Happy-path DAG drawn
- [ ] Node types assigned
- [ ] Parallel joins and optional paths defined
- [ ] Human candidates and organizations defined
- [ ] Automation handlers identified
- [ ] Signals and correlation keys defined
- [ ] Timers and failure policies defined

Data boundary:

- [ ] Only minimal orchestration facts stored
- [ ] Large and sensitive data stays in domain system
- [ ] Opaque result-reference format defined
- [ ] Retention and classification reviewed

Reliability:

- [ ] Command IDs generated and retained
- [ ] Consumers deduplicate event IDs
- [ ] External effects are idempotent
- [ ] Retry and dead-letter operations documented
- [ ] Recovery and reconciliation tested

Governance:

- [ ] Definition owner named
- [ ] Publication process defined
- [ ] Permissions and exceptional operations reviewed
- [ ] Monitoring and support ownership assigned

## 18. Reusable mapping

~~~text
Business process concept          Flow concept

Process definition                WORKFLOW_VERSION
Business lifecycle                Workflow lifecycle FSM
Activity dependencies             DAG
Unit of work behavior             Step FSM
Long-running sub-process          Child workflow
Routing input                     Controlled fact
External occurrence               Signal
Human queue                       Candidates + assignments
Service call                      Automation job
Deadline/wait                     Durable timer
Execution audit                   Workflow event
Outbound notification             Outbox event
Inbound provider event            Inbox event
Retry identity                    Command/event/job key
~~~

The mapping remains the same whether the process is an order, onboarding journey, service activation, manufacturing job, incident response, or security review.
