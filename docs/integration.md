# Integration contract

Domain applications interact with the engine through commands and events.

## Start command

`POST /api/workflows`

```json
{
  "command_id": "a-client-generated-uuid",
  "workflow_version_id": 1,
  "title": "Review ASMT-502",
  "business_type": "ISR_ASSESSMENT",
  "business_key": "ASMT-502",
  "correlation_id": "ISR-REQ-200",
  "actor": "isr.service",
  "variables": {"identity_review_required": true},
  "subjects": []
}
```

Sending the same `command_id` again returns the original result without creating a second workflow.

## Step command

`POST /api/steps/{step_instance_id}/actions`

```json
{
  "command_id": "another-client-generated-uuid",
  "action": "complete",
  "actor": "user-123",
  "expected_revision": 4,
  "payload": {"domain_result_reference": "SME-REVIEW-701"}
}
```

The domain result stays in the client system. `expected_revision` protects against concurrent changes.

## Events

Register a webhook with `POST /api/webhook-subscriptions`. Events include an immutable event ID, per-workflow sequence number, business reference, correlation ID, actor, state change, and payload. A configured secret produces an `X-Workflow-Signature` HMAC header.

