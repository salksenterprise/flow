from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from workflow_core import WorkflowEngine
from workflow_sqlite import SQLiteWorkflowRepository


DATABASE_PATH = Path(os.environ.get("WORKFLOW_DB_PATH", "/data/workflow.db"))
POLL_SECONDS = float(os.environ.get("WORKFLOW_POLL_SECONDS", "2"))
repository = SQLiteWorkflowRepository(DATABASE_PATH)
engine = WorkflowEngine(repository)


def accepts(subscription: dict, event_type: str) -> bool:
    configured = subscription.get("event_types", [])
    return not configured or event_type in configured


def signature(secret: str | None, body: bytes) -> str | None:
    if not secret:
        return None
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def deliver(event: dict, subscription: dict) -> tuple[bool, str | None]:
    body = json.dumps(event["payload"], separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Workflow-Event": event["event_type"],
        "X-Workflow-Event-Id": event["event_id"],
    }
    digest = signature(subscription.get("secret"), body)
    if digest:
        headers["X-Workflow-Signature"] = digest
    request = urllib.request.Request(subscription["target_url"], data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if 200 <= response.status < 300:
                return True, None
            return False, f"HTTP {response.status}"
    except (urllib.error.URLError, TimeoutError) as error:
        return False, str(error)


def run_once() -> int:
    # The worker is the only caller that needs signing secrets.
    subscriptions = repository.list_subscriptions(active_only=True, include_secrets=True)
    processed = 0
    for event in repository.pending_outbox():
        # A retry must not re-post to a subscription that already accepted the
        # event, or every failing subscriber turns into duplicate deliveries
        # for every healthy one.
        already_delivered = repository.delivered_subscription_ids(event["id"])
        targets = [item for item in subscriptions
                   if accepts(item, event["event_type"]) and item["id"] not in already_delivered]
        success = True
        for target in targets:
            delivered, error = deliver(event, target)
            repository.record_delivery(event["id"], target["id"], delivered, error)
            success = success and delivered
        repository.mark_outbox(event["id"], success, None if success else "One or more deliveries failed")
        processed += 1
    return processed + engine.process_due_timers()


def main() -> None:
    repository.initialize()
    while True:
        run_once()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
