from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from isrp.api import make_server
from isrp.application import ISRPApplication


class ISRPAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        application = ISRPApplication(Path(self.temp.name) / "isrp.db")
        application.initialize()
        self.server = make_server(application, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def call(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.load(response)

    def test_http_vertical_slice(self):
        status, health = self.call("GET", "/api/health")
        self.assertEqual((status, health["status"]), (200, "ok"))

        status, request = self.call("POST", "/api/requests", {
            "command_id": str(uuid.uuid4()),
            "title": "Payments modernization",
            "summary": "Replace the payment routing layer.",
            "requester_name": "Morgan Chen",
            "requester_email": "morgan@example.com",
            "organization_name": "Payments",
        })
        self.assertEqual(status, 201)
        self.assertEqual(request["lifecycle_status"], "DRAFT")

        status, request = self.call("POST", f"/api/requests/{request['id']}/submit", {
            "command_id": str(uuid.uuid4()), "expected_revision": request["revision"],
        })
        self.assertEqual((status, request["lifecycle_status"]), (200, "SUBMITTED"))

        status, assessment = self.call(
            "POST", f"/api/requests/{request['id']}/assessments", {
                "command_id": str(uuid.uuid4()),
                "title": "Application assessment",
                "assessment_type": "APPLICATION",
                "expected_revision": request["revision"],
            })
        self.assertEqual(status, 201)
        self.assertEqual(assessment["assessment_type"], "APPLICATION")

        _, work = self.call("GET", "/api/work")
        self.assertEqual(work["total"], 2)
        _, dashboard = self.call("GET", "/api/dashboard")
        self.assertEqual(dashboard, {
            "requests": 1, "submitted": 1, "assessments": 1, "active_work": 2,
        })

    def test_validation_errors_are_json(self):
        request = urllib.request.Request(
            self.base + "/api/requests", data=b"{}", method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(raised.exception.code, 400)
        self.assertIn("title", json.load(raised.exception)["error"])


if __name__ == "__main__":
    unittest.main()
