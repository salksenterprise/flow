"""The HTTP shell end to end, with the security boundary as the point.

NFR-5 is the reason this file exists: a client must not be able to grant itself
a permission by putting one in the request body. Testing that through the real
application, rather than against the helper alone, is the only way to know the
wiring is right.
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - the service shell is optional
    TestClient = None


TEMPLATE = {
    "key": "api-approval", "name": "API approval", "version": 1, "publish": True,
    "lifecycle_fsm": {
        "key": "api.lifecycle", "name": "API lifecycle", "version": 1,
        "initial_state": "OPEN",
        "states": ["OPEN", {"key": "CLOSED", "terminal": True}],
        "transitions": [{"action": "close", "from": "OPEN", "to": "CLOSED",
                         "required_permission": "workflow.close"}],
    },
    "steps": [
        {"key": "review", "name": "Review", "type": "HUMAN_TASK", "configuration": {}},
        {"key": "end", "name": "End", "type": "END"},
    ],
    "transitions": [{"from_step": "review", "to_step": "end"}],
}


@unittest.skipIf(TestClient is None, "fastapi is not installed")
class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.environ["WORKFLOW_DB_PATH"] = str(Path(self.temp.name) / "api.db")
        os.environ["WORKFLOW_EXAMPLES_PATH"] = str(Path(self.temp.name) / "no-examples")
        import app.config, app.main
        importlib.reload(app.config)
        self.module = importlib.reload(app.main)
        self.client = TestClient(self.module.app)
        self.client.__enter__()
        self.version = self.client.post(
            "/api/templates/import", json=TEMPLATE).json()["workflow_version_id"]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def start(self, **overrides):
        body = {"command_id": str(uuid.uuid4()), "workflow_version_id": self.version,
                "title": "Review", "business_type": "APPROVAL",
                "business_key": str(uuid.uuid4())}
        body.update(overrides)
        response = self.client.post("/api/workflows", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    @staticmethod
    def header(**claims):
        return {"x-flow-actor": json.dumps(claims)}

    # NFR-5: authority comes from the gateway, never the body.

    def test_nfr5_permission_in_the_request_body_is_ignored(self):
        workflow = self.start()
        response = self.client.post(
            f"/api/workflows/{workflow['id']}/actions",
            json={"command_id": str(uuid.uuid4()), "action": "close",
                  "expected_revision": workflow["revision"],
                  "actor": {"actor_id": "mallory", "permissions": ["workflow.close"]}})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("permission", response.text.lower())

    def test_nfr5_permission_in_the_trusted_header_is_honoured(self):
        workflow = self.start()
        response = self.client.post(
            f"/api/workflows/{workflow['id']}/actions",
            json={"command_id": str(uuid.uuid4()), "action": "close",
                  "expected_revision": workflow["revision"]},
            headers=self.header(actor_id="alice", permissions=["workflow.close"]))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["lifecycle_state"], "CLOSED")

    def test_nfr5_malformed_actor_header_is_a_bad_request(self):
        workflow = self.start()
        response = self.client.post(
            f"/api/workflows/{workflow['id']}/actions",
            json={"command_id": str(uuid.uuid4()), "action": "close"},
            headers={"x-flow-actor": "{not json"})
        self.assertEqual(response.status_code, 400)

    def test_nfr5_repair_is_refused_without_the_repair_permission(self):
        workflow = self.start()
        step = next(item for item in workflow["steps"] if item["step_key"] == "review")
        refused = self.client.post(
            f"/api/steps/{step['id']}/repair",
            json={"action": "skip_step", "reason": "trying it on"},
            headers=self.header(actor_id="mallory"))
        self.assertEqual(refused.status_code, 409)

        allowed = self.client.post(
            f"/api/steps/{step['id']}/repair",
            json={"action": "skip_step", "reason": "Vendor unresponsive"},
            headers=self.header(actor_id="ops", permissions=["workflow.repair"]))
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(allowed.json()["execution_status"], "COMPLETED")

    # OPS-5: secrets are write-only.

    def test_ops5_subscription_read_does_not_return_the_secret(self):
        created = self.client.post("/api/webhook-subscriptions", json={
            "name": "hook", "target_url": "https://example.test/hook",
            "event_types": [], "secret": "super-secret-value"})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertNotIn("super-secret-value", created.text)
        listed = self.client.get("/api/webhook-subscriptions")
        self.assertNotIn("super-secret-value", listed.text)
        self.assertTrue(listed.json()[0]["has_secret"])

    # WRK-6, WRK-7, OPS-2, OPS-6: the operations surface.

    def test_work_queue_is_filterable_and_paginated(self):
        for _ in range(3):
            self.start()
        page = self.client.get("/api/work?limit=2").json()
        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["items"]), 2)
        self.assertEqual(page["limit"], 2)

    def test_operations_endpoints_report_counters_and_stuck_workflows(self):
        self.start()
        counters = self.client.get("/api/operations/counters").json()
        self.assertEqual(counters["workflows_running"], 1)
        self.assertEqual(self.client.get("/api/operations/stuck").json(), [])
        self.assertEqual(self.client.get("/api/operations/dead-letters").json(), [])
        self.assertEqual(
            self.client.post("/api/operations/dead-letters/999/redrive").status_code, 404)

    # EMB-4 surfaces as a mapped status rather than a stack trace.

    def test_duplicate_template_version_is_a_conflict_not_a_crash(self):
        response = self.client.post("/api/templates/import", json=TEMPLATE)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("already exists", response.text)

    def test_unknown_workflow_is_a_not_found(self):
        self.assertEqual(self.client.get("/api/workflows/424242").status_code, 404)


if __name__ == "__main__":
    unittest.main()
