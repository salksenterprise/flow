# Integration contract

Domain applications interact with Flow through idempotent commands and events. Domain records remain in the client system; Flow stores opaque references.

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
  "actor": {
    "actor_id": "user-123",
    "organization_id": "security",
    "roles": ["PRIMARY_REVIEWER"],
    "permissions": []
  },
  "variables": {},
  "subjects": []
}
~~~

Repeating a completed `command_id` returns the original result.

## Workflow and step state

Flow keeps three state dimensions separate:

~~~text
workflow.lifecycle_state
workflow.execution_status
step.state + step.execution_status
~~~

Lifecycle and step state transitions come from immutable FSM versions. Execution status is controlled by the engine.

## Parent-child workflows

`POST /api/workflows/{parent_id}/children`

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

`POST /api/steps/{step_instance_id}/actions`

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

Register a webhook with `POST /api/webhook-subscriptions`. Events contain an immutable event ID, per-workflow sequence number, business reference, correlation ID, actor, state change, and payload.

Configured secrets produce an `X-Workflow-Signature` HMAC header. Delivery uses claims, retry backoff, and dead-letter status.

## ISRP boundary

Flow does not store requirements, responses, evidence, findings, remediation cases, CAPs, or issue-management records. ISRP supplies opaque references and signals; Flow supplies durable orchestration.
