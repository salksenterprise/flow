# Integration contract

> **Superseded.** ISRP now owns its orchestration directly; there is no separate
> workflow engine, service or package. This document described that engine and is
> retained only until its content has been folded into the ISRP design set. See
> [ISRP Orchestration](isrp/orchestration.md) for the current design.

Domain applications interact with Flow through idempotent commands and events.
Domain records remain in the client system; Flow stores opaque references.

This describes the HTTP service shell. An application that can embed Flow should
prefer to, because a domain write and a workflow transition then commit in one
transaction; see [the technical design](technical-design.md) and
`examples/embedded-host`.

## Identity

Flow does not accept an actor's authority from the request body. Roles, groups
and permissions sent there are discarded. A gateway authenticates the caller and
injects a trusted header, which the shell reads instead:

~~~text
x-flow-actor: {"actor_id":"alice","organization_id":"security",
               "roles":["PRIMARY_REVIEWER"],"permissions":["workflow.repair"]}
~~~

The header name is configurable with `WORKFLOW_ACTOR_HEADER`. With no header the
caller is anonymous and holds no permissions, so privileged transitions are
refused. A misconfigured deployment therefore fails closed.

## Definitions

~~~text
GET  /api/templates                    published and draft versions
GET  /api/templates/{version_id}       one version, with steps and transitions
POST /api/templates/import             publish a new definition version
~~~

Publishing validates the graph, the FSMs, the guards and the step configuration.
A version number that already exists is a 409: published versions are immutable,
so publish a new number instead.

## Reading workflows

~~~text
GET  /api/workflows                    all instances, newest first
GET  /api/workflows/{workflow_id}      one instance with steps, subjects,
                                       children, assignments and event history
~~~

## Start a workflow

`POST /api/workflows`

~~~json
{
  "command_id": "client-generated-uuid",
  "workflow_version_id": 1,
  "title": "Review ASMT-502",
  "business_type": "ISR_ASSESSMENT",
  "business_key": "ASMT-502",
  "correlation_id": "ISR-REQ-200",
  "variables": {},
  "subjects": []
}
~~~

Repeating a completed `command_id` has one effect and returns the workflow's
**current** state, not a replay of the original response. A client that retries
after a timeout therefore learns where things stand now, which is usually what
it needed. The idempotency check runs before revision validation, so a retry
never comes back as a conflict.

## Workflow and step state

Flow keeps three state dimensions separate:

~~~text
workflow.lifecycle_state
workflow.execution_status
step.state + step.execution_status
~~~

Lifecycle and step state transitions come from immutable FSM versions. Execution status is controlled by the engine.

## Parent-child workflows

`POST /api/workflows/{workflow_id}/children`

The request body is a normal workflow-start command plus:

~~~json
{
  "parent_step_instance_id": 42,
  "relationship_type": "ASSESSMENT",
  "relationship_key": "EXTERNAL",
  "required": true
}
~~~

The parent `SUBWORKFLOW` node waits for required children. Flow treats relationship values as opaque.

## Step commands

`POST /api/steps/{step_id}/actions`

~~~json
{
  "command_id": "uuid",
  "action": "complete",
  "actor": "user-123",
  "expected_revision": 4,
  "reason": null,
  "payload": {"domain_result_reference": "isrp://work-packages/WP-701"}
}
~~~

The allowed action and target state are resolved from the step's FSM version.

## Workflow lifecycle commands

`POST /api/workflows/{workflow_id}/actions`

Generic execution operations:

~~~text
suspend
resume
terminate
~~~

Other actions, such as `submit`, `close`, `cancel`, or `reopen`, are resolved through the configured workflow lifecycle FSM.

## Controlled facts

`POST /api/workflows/{workflow_id}/facts`

~~~json
{
  "command_id": "uuid",
  "expected_revision": 5,
  "facts": {"identity_review_required": true},
  "source_type": "ISRP",
  "source_reference": "ISR-REQ-200"
}
~~~

Every changed fact records its previous value, new value, source, actor, and workflow revision. DAG guards use these facts and cannot execute arbitrary code.

## Signals

`POST /api/workflows/{workflow_id}/signals`

~~~json
{
  "command_id": "uuid",
  "signal_type": "EXTERNAL_ISSUE_RESOLVED",
  "correlation_key": "ISS-100",
  "payload": {"result_reference": "isrp://issues/ISS-100"}
}
~~~

Signals are durable and idempotent. They may arrive before or after a matching `WAIT_SIGNAL` node becomes active.

## Connector inbox

`POST /api/workflows/{workflow_id}/external-events`

~~~json
{
  "connector_name": "issue-manager",
  "provider_event_id": "provider-event-991",
  "event_type": "EXTERNAL_ISSUE_RESOLVED",
  "correlation_key": "ISS-100",
  "payload": {}
}
~~~

`connector_name + provider_event_id` is unique. Accepted events are translated into signals using the same idempotent command path.

## Automation jobs

Workers claim jobs:

~~~text
GET /api/automation/jobs?worker_id=worker-1&limit=10
~~~

Workers report results:

~~~text
POST /api/automation/jobs/{job_id}/result
~~~

Jobs use leases, bounded attempts, retry waiting, and immutable workflow events. Automation results contain opaque domain references.

## Timers

`TIMER` nodes create durable timer records. The worker processes due timers, and the administrative API exposes:

~~~text
POST /api/timers/process
~~~

Timer firing is idempotent.

## Events and webhooks

~~~text
GET  /api/webhook-subscriptions        secrets are never returned
POST /api/webhook-subscriptions
~~~

A subscription with an empty `event_types` receives every event. Reads return
`has_secret` in place of the signing secret. Events contain an immutable event ID, per-workflow sequence number, business reference, correlation ID, actor, state change, and payload.

Every event envelope carries a `schema_version`, so a consumer can tell which
shape it is reading.

Configured secrets produce an `X-Workflow-Signature` HMAC header. Delivery uses
claims, retry backoff and dead-letter status, and a retry redelivers only to
subscriptions that have not already accepted the event.

## Health

~~~text
GET /api/health
~~~

## Work queues

~~~text
GET /api/work?assignee=alice
GET /api/work?mine=true                  uses the caller's candidate rules
GET /api/work?business_key=ASMT-502&limit=50&offset=0
~~~

Filters: `assignee`, `mine`, `execution_status`, `step_type`, `business_type`,
`business_key`, `workflow_id`. Results carry `items`, `total`, `limit`, `offset`.

## Version migration

`POST /api/workflows/{workflow_id}/migrate-version`

~~~json
{"target_version_id": 7, "expected_revision": 4, "reason": "Policy change approved"}
~~~

Requires `workflow.migrate`. Refused while any node is active or waiting, and
only between versions of one definition. Publishing a version never migrates a
running instance on its own.

## Operations

~~~text
GET  /api/operations/counters                      queue depth, failures, lag
GET  /api/operations/stuck                         workflows nothing can advance
GET  /api/operations/dead-letters                  exhausted deliveries
POST /api/operations/dead-letters/{outbox_id}/redrive
POST /api/steps/{step_id}/repair                   authorized manual intervention
~~~

Repair takes an action of `skip_step`, `force_complete_step`, `retry_step` or
`reassign_step`, and requires `workflow.repair` and a reason. Every use is
recorded as an event.

## ISRP boundary

Flow does not store requirements, responses, evidence, findings, remediation cases, CAPs, or issue-management records. ISRP supplies opaque references and signals; Flow supplies durable orchestration.
