"""A reference application that embeds Flow.

This is the shape the charter commits to. There is no workflow service, no
network hop and no second database. The application owns the connection and
decides where the transaction ends; Flow joins it.

The payoff is the `decide` method below. Recording the business decision and
advancing the workflow are one commit, so the two can never disagree. Across a
service boundary the same operation needs a saga, a compensating action and a
reconciliation job.

Run it:

    PYTHONPATH=packages/workflow-core/src:packages/workflow-sqlite/src \
        python examples/embedded-host/host.py
"""

from __future__ import annotations

import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from workflow_core import WorkflowEngine
from workflow_sqlite import SQLiteWorkflowRepository


# The host's own table. workflow_instance_id is a real foreign key, enforced by
# the database, because Flow's tables live here too. A separate workflow service
# could only offer an opaque identifier that nothing validates.
DOMAIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_request (
  reference TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  decision TEXT,
  workflow_instance_id INTEGER NOT NULL REFERENCES workflow_instance(id),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

WORKFLOW = {
    "key": "embedded-approval",
    "name": "Embedded approval",
    "description": "Reference workflow for the embedded host example.",
    "domain": "EXAMPLE",
    "version": 1,
    "publish": True,
    "steps": [
        {"key": "review", "name": "Review request", "type": "HUMAN_TASK",
         "stage": "Review", "assignment_role": "APPROVER"},
        {"key": "end", "name": "Complete", "type": "END", "stage": "End"},
    ],
    "transitions": [{"from_step": "review", "to_step": "end"}],
}


class HostFailure(RuntimeError):
    """Raised by the host after its writes, to demonstrate rollback."""


class ApprovalApp:
    """A small domain application with Flow embedded inside it."""

    def __init__(self, path: str | Path):
        self.connection = sqlite3.connect(str(path), isolation_level=None, timeout=30.0)
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self.connection.execute("PRAGMA foreign_keys = ON")
        # No path: this repository never opens a connection of its own.
        self.repository = SQLiteWorkflowRepository()
        self.engine = WorkflowEngine(self.repository)
        self.workflow_version_id: int | None = None

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One transaction spanning the host's tables and Flow's."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            with self.repository.using(self.connection):
                yield self.connection
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def migrate(self) -> None:
        """Everything the host runs once at deploy time.

        Flow's schema installs through the host's own migration step, so both
        sets of tables are versioned and deployed together.
        """
        self.repository.create_schema(self.connection)
        self.connection.executescript(DOMAIN_SCHEMA)
        self.connection.commit()
        with self.transaction():
            templates = self.engine.list_templates()
            if not any(item["key"] == WORKFLOW["key"] for item in templates):
                self.engine.import_template(WORKFLOW)
                templates = self.engine.list_templates()
            self.workflow_version_id = next(
                item["workflow_version_id"] for item in templates
                if item["key"] == WORKFLOW["key"])

    def submit(self, reference: str, title: str) -> int:
        """Create the business record and its workflow in one commit."""
        with self.transaction() as connection:
            workflow = self.engine.start_workflow({
                "command_id": f"submit:{reference}",
                "workflow_version_id": self.workflow_version_id,
                "title": title,
                "business_type": "APPROVAL_REQUEST",
                "business_key": reference,
                "actor": "approval.app",
                "variables": {}, "subjects": [],
            })
            connection.execute(
                """INSERT INTO approval_request(reference,title,workflow_instance_id)
                   VALUES (?,?,?)""",
                (reference, title, workflow["id"]),
            )
            return workflow["id"]

    def decide(self, reference: str, decision: str, then_fail: bool = False) -> None:
        """Record the decision and advance the workflow, atomically.

        then_fail makes the host raise after both writes, to show that neither
        survives.
        """
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT workflow_instance_id FROM approval_request WHERE reference=?",
                (reference,),
            ).fetchone()
            if row is None:
                raise LookupError(f"No approval request {reference}")

            workflow = self.engine.get_workflow(row["workflow_instance_id"])
            step = next(item for item in workflow["steps"]
                        if item["step_key"] == "review")

            connection.execute(
                """UPDATE approval_request SET decision=?,updated_at=CURRENT_TIMESTAMP
                   WHERE reference=?""",
                (decision, reference),
            )
            workflow = self.engine.apply_action(step["id"], {
                "command_id": f"start:{reference}", "action": "start",
                "actor": "approval.app", "expected_revision": workflow["revision"],
                "payload": {},
            })
            self.engine.apply_action(step["id"], {
                "command_id": f"complete:{reference}", "action": "complete",
                "actor": "approval.app", "expected_revision": workflow["revision"],
                "payload": {"decision_reference": f"approval://requests/{reference}"},
            })
            if then_fail:
                raise HostFailure("host aborted after writing both sides")

    def snapshot(self, reference: str) -> dict[str, Any]:
        """The business record and its workflow, read together."""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT decision,workflow_instance_id FROM approval_request WHERE reference=?",
                (reference,),
            ).fetchone()
            if row is None:
                return {}
            workflow = self.engine.get_workflow(row["workflow_instance_id"])
            return {
                "decision": row["decision"],
                "execution_status": workflow["execution_status"],
                "review": next(item["execution_status"] for item in workflow["steps"]
                               if item["step_key"] == "review"),
            }


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = ApprovalApp(Path(directory) / "approvals.db")
        app.migrate()

        app.submit("REQ-1", "New laptop")
        print("after submit :", app.snapshot("REQ-1"))

        try:
            app.decide("REQ-1", "APPROVED", then_fail=True)
        except HostFailure as error:
            print("host failed  :", error)
        print("after failure:", app.snapshot("REQ-1"), "<- neither side moved")

        app.decide("REQ-1", "APPROVED")
        print("after decide :", app.snapshot("REQ-1"), "<- both sides moved")
        app.close()


if __name__ == "__main__":
    main()
