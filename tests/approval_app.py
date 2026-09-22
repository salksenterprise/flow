"""A miniature application with orchestration inside it, used as a test fixture.

It stands in for ISRP: it owns the connection, decides where the transaction
ends, and calls the orchestration engine inside it. The payoff is the `decide`
method below, where recording the business decision and advancing the workflow
are one commit, so the two can never disagree.

That property is the reason this fixture exists. It is what the fused design
buys and what tests/test_embedding.py holds it to.

Run it:

    PYTHONPATH=. python tests/approval_app.py
"""

from __future__ import annotations

import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from isrp.orchestration import WorkflowEngine
from isrp.orchestration import SQLiteWorkflowRepository


# A stand-in for a domain table. request_id is a real foreign key, enforced by
# the database, because the orchestration state lives on isrp_request itself.
DOMAIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_request (
  reference TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  decision TEXT,
  request_id INTEGER NOT NULL REFERENCES isrp_request(id),
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

WORKFLOW = {
    "key": "fused-approval",
    "name": "Fused approval",
    "description": "Reference process for the fused application fixture.",
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
    """A small domain application that orchestrates its own work."""

    def __init__(self, path: str | Path):
        self.connection = sqlite3.connect(str(path), isolation_level=None, timeout=30.0)
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self.connection.execute("PRAGMA foreign_keys = ON")
        # No path: the store never opens a connection of its own.
        self.repository = SQLiteWorkflowRepository()
        self.engine = WorkflowEngine(self.repository)
        self.workflow_version_id: int | None = None

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One transaction spanning domain and orchestration tables."""
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

        The orchestration schema installs through the application's migration
        step, so all tables are versioned and deployed together.
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
        """Create the business record and open its request in one commit."""
        with self.transaction() as connection:
            workflow = self.engine.start_request({
                "command_id": f"submit:{reference}",
                "workflow_version_id": self.workflow_version_id,
                "title": title,
                "actor": "approval.app",
                "variables": {},
            })
            connection.execute(
                """INSERT INTO approval_request(reference,title,request_id)
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
                "SELECT request_id FROM approval_request WHERE reference=?",
                (reference,),
            ).fetchone()
            if row is None:
                raise LookupError(f"No approval request {reference}")

            workflow = self.engine.get_request(row["request_id"])
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
                "SELECT decision,request_id FROM approval_request WHERE reference=?",
                (reference,),
            ).fetchone()
            if row is None:
                return {}
            workflow = self.engine.get_request(row["request_id"])
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
