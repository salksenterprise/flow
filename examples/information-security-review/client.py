"""Tiny external ISR client demonstrating the integration boundary."""

from __future__ import annotations

import json
import urllib.request
import uuid


API = "http://localhost:8000/api"


def request(path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    return json.load(urllib.request.urlopen(req))


templates = request("/templates")
version = next(item for item in templates if item["key"] == "information-security-review")

# ASMT-502 remains in the external ISR system. The workflow service receives
# only a business reference and routing variables.
workflow = request("/workflows", "POST", {
    "command_id": str(uuid.uuid4()),
    "workflow_version_id": version["workflow_version_id"],
    "title": "Review ASMT-502",
    "business_type": "ISR_ASSESSMENT",
    "business_key": "ASMT-502",
    "correlation_id": "ISR-REQ-200",
    "actor": "isr.service",
    "variables": {
        "identity_review_required": True,
        "network_review_required": True
    },
    "subjects": [
        {"subject_type": "APPLICATION", "subject_id": "APP-100", "source_system": "application-registry"},
        {"subject_type": "TECHNOLOGY", "subject_id": "TECH-200", "source_system": "technology-catalogue"}
    ]
})

print(json.dumps({
    "workflow_instance_id": workflow["id"],
    "business_key": workflow["business_key"],
    "revision": workflow["revision"],
    "ready_steps": [step["step_key"] for step in workflow["steps"] if step["state"] == "READY"]
}, indent=2))

