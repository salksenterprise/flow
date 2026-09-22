from __future__ import annotations

import tempfile
import unittest
import uuid
import sqlite3
from pathlib import Path

from isrp.application import ISRPApplication
from isrp.orchestration import ConflictError, ValidationError
from isrp.orchestration import SQLiteWorkflowRepository
from isrp.orchestration.schema import SCHEMA_VERSION


def command(**values):
    return {"command_id": str(uuid.uuid4()), **values}


class ISRPApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = ISRPApplication(Path(self.temp.name) / "isrp.db")
        self.app.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def create(self, **overrides):
        data = command(
            title="Customer analytics platform",
            summary="New service processing customer behavior data.",
            requester_name="Avery Morgan",
            requester_email="avery@example.com",
            organization_name="Digital Products",
            identity_review_required=True,
        )
        data.update(overrides)
        return self.app.create_request(data, "avery")

    def test_vertical_slice_from_draft_to_assessment_work(self):
        request = self.create()
        self.assertEqual(request["reference"], "ISR-000001")
        self.assertEqual(request["lifecycle_status"], "DRAFT")
        self.assertEqual(request["steps"][0]["execution_status"], "WAITING")
        self.assertEqual(self.app.dashboard()["active_work"], 0)

        request = self.app.submit_request(
            request["id"], command(expected_revision=request["revision"]), "avery")
        self.assertEqual(request["lifecycle_status"], "SUBMITTED")
        self.assertIsNotNone(request["submitted_at"])
        coordinator = next(step for step in request["steps"]
                           if step["step_key"] == "coordinate_review")
        self.assertEqual(coordinator["execution_status"], "READY")

        assessment = self.app.create_assessment(request["id"], command(
            title="Application security assessment",
            assessment_type="APPLICATION",
            expected_revision=request["revision"],
        ), "reviewer")
        self.assertEqual(assessment["request_id"], request["id"])
        self.assertEqual(assessment["assessment_type"], "APPLICATION")
        self.assertEqual(assessment["steps"][0]["execution_status"], "READY")

        refreshed = self.app.get_request(request["id"])
        self.assertEqual(refreshed["lifecycle_status"], "IN_REVIEW")
        self.assertEqual(len(refreshed["assessments"]), 1)
        work = self.app.list_work()
        self.assertEqual(work["total"], 2)
        self.assertEqual(
            {item["owner_type"] for item in work["items"]},
            {"ISRP_REQUEST", "ISRP_ASSESSMENT"},
        )
        coordination = next(item for item in work["items"]
                            if item["owner_type"] == "ISRP_REQUEST")
        aggregate = self.app.apply_work_action(coordination["step_instance_id"], command(
            action="start", expected_revision=coordination["owner_revision"]), "coordinator")
        aggregate = self.app.apply_work_action(coordination["step_instance_id"], command(
            action="complete", expected_revision=aggregate["revision"]), "coordinator")
        self.assertEqual(aggregate["execution_status"], "RUNNING")
        waiting = next(step for step in aggregate["steps"]
                       if step["step_key"] == "await_completion")
        self.assertEqual(waiting["execution_status"], "WAITING")

    def test_create_is_idempotent_and_reference_is_unique(self):
        data = command(
            reference="ISR-CUSTOM",
            title="Identity modernization",
            requester_name="Jordan Lee",
            requester_email="jordan@example.com",
        )
        first = self.app.create_request(data, "jordan")
        second = self.app.create_request(data, "jordan")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.app.list_requests()), 1)
        with self.assertRaises(ConflictError):
            self.create(reference="ISR-CUSTOM")

    def test_assessment_requires_a_submitted_request(self):
        request = self.create()
        with self.assertRaises(ConflictError):
            self.app.create_assessment(request["id"], command(
                title="Architecture review", assessment_type="ARCHITECTURE"), "reviewer")

    def test_validates_intake_and_assessment_type(self):
        with self.assertRaises(ValidationError):
            self.create(requester_email="not-an-email")
        request = self.create()
        request = self.app.submit_request(request["id"], command(), "avery")
        with self.assertRaises(ValidationError):
            self.app.create_assessment(request["id"], command(
                title="Unknown", assessment_type="OTHER"), "reviewer")


class RequestIntakeMigrationTests(unittest.TestCase):
    def test_version_eight_database_adds_intake_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v8.db"
            connection = sqlite3.connect(path, isolation_level=None)
            connection.executescript("""
                CREATE TABLE schema_metadata (
                  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);
                INSERT INTO schema_metadata(key,value) VALUES ('schema_version','8');
                CREATE TABLE isrp_request (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  workflow_version_id INTEGER NOT NULL,
                  title TEXT NOT NULL,
                  lifecycle_status TEXT NOT NULL,
                  execution_status TEXT NOT NULL DEFAULT 'RUNNING',
                  current_stage TEXT, revision INTEGER NOT NULL DEFAULT 0,
                  variables_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL,
                  created_at TEXT NOT NULL, completed_at TEXT, suspended_at TEXT,
                  cancelled_at TEXT
                );
            """)
            SQLiteWorkflowRepository(path).create_schema(connection)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(isrp_request)")}
            version = connection.execute(
                "SELECT value FROM schema_metadata WHERE key='schema_version'").fetchone()[0]
            connection.close()
            self.assertTrue({
                "reference", "summary", "requester_name", "requester_email",
                "organization_name", "source_system", "submitted_at",
            }.issubset(columns))
            self.assertEqual(version, str(SCHEMA_VERSION))


if __name__ == "__main__":
    unittest.main()
