"""The contract every persistence adapter must satisfy (NFR-2).

SQLite is the development adapter and Oracle is the production one. They must
behave identically, and the only way to know that is to hold them to one suite
rather than two.

This covers the shared reliability surface too: an adapter must let an
embedding host write its own domain events, outbox rows and provider receipts
into the same tables Flow uses.

To certify a new adapter, subclass RepositoryContract with a TestCase, return
your adapter from make_repository(), and run it. Nothing in here is specific to
SQLite: the tests go through the repository interface and the engine, never
through dialect-specific SQL.

    class OracleContractTests(RepositoryContract, unittest.TestCase):
        def make_repository(self):
            return OracleWorkflowRepository(dsn=os.environ["ORACLE_DSN"])
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from isrp.orchestration import WorkflowEngine

from support import aggregate_args, owner


def command(**values):
    return {"command_id": str(uuid.uuid4()), "actor": "contract", **values}


HUMAN = {
    "key": "contract-human", "name": "Contract human", "version": 1, "publish": True,
    "steps": [
        {"key": "work", "name": "Work", "type": "HUMAN_TASK",
         "configuration": {"candidates": [
             {"type": "GROUP", "value": "REVIEWERS", "organization_id": "ORG-A"}]}},
        {"key": "end", "name": "End", "type": "END"},
    ],
    "transitions": [{"from_step": "work", "to_step": "end"}],
}

AUTOMATED = {
    "key": "contract-automated", "name": "Contract automated", "version": 1, "publish": True,
    "steps": [
        {"key": "work", "name": "Work", "type": "AUTOMATED_TASK",
         "configuration": {"handler": "noop"}},
        {"key": "end", "name": "End", "type": "END"},
    ],
    "transitions": [{"from_step": "work", "to_step": "end"}],
}

WAITING = {
    "key": "contract-signal", "name": "Contract signal", "version": 1, "publish": True,
    "steps": [
        {"key": "work", "name": "Work", "type": "WAIT_SIGNAL",
         "configuration": {"signal_type": "READY"}},
        {"key": "end", "name": "End", "type": "END"},
    ],
    "transitions": [{"from_step": "work", "to_step": "end"}],
}


class RepositoryContract:
    """Behaviour shared by every adapter. Subclass with a TestCase."""

    def make_repository(self) -> Any:
        raise NotImplementedError("Return a fresh, initialised repository")

    def setUp(self):
        super().setUp()
        self.repository = self.make_repository()
        self.engine = WorkflowEngine(self.repository)
        self._versions: dict[str, int] = {}

    def version_for(self, template) -> int:
        """Publish once per template. A second publish of the same version is
        a conflict by design, so the helper must not provoke one."""
        key = template["key"]
        if key not in self._versions:
            self._versions[key] = self.engine.import_template(template)["workflow_version_id"]
        return self._versions[key]

    def start(self, template):
        version = self.version_for(template)
        return self.engine.start_request(command(
            workflow_version_id=version, title="Contract",
            variables={}))

    def scalar(self, sql, *args):
        with self.repository.transaction():
            return self.repository.db.execute(sql, args).fetchone()[0]

    # Schema

    def test_contract_schema_creation_is_idempotent(self):
        workflow = self.start(HUMAN)
        self.repository.initialize()
        self.assertEqual(
            self.engine.get_aggregate(owner(workflow))["execution_status"], "RUNNING")

    # Transactions

    def test_contract_a_failed_transaction_discards_its_writes(self):
        workflow = self.start(HUMAN)
        before = self.scalar(
            "SELECT COUNT(*) FROM event_log WHERE aggregate_type=? AND aggregate_id=?",
            *aggregate_args(workflow))

        class Boom(Exception):
            pass

        with self.assertRaises(Boom):
            with self.repository.transaction():
                self.repository.append_event(owner(workflow), "CONTRACT_PROBE", "contract")
                raise Boom()

        self.assertEqual(
            self.scalar("SELECT COUNT(*) FROM event_log WHERE aggregate_type=? AND aggregate_id=?",
                        *aggregate_args(workflow)),
            before)

    # Idempotency

    def test_contract_a_command_receipt_round_trips(self):
        workflow = self.start(HUMAN)
        identifier = str(uuid.uuid4())
        with self.repository.transaction():
            self.repository.record_command(identifier, owner(workflow), "probe", 7)
            receipt = self.repository.command_result(identifier)
        self.assertEqual(
            (receipt["owner_type"], receipt["owner_id"]), owner(workflow))
        self.assertEqual(receipt["revision"], 7)
        with self.repository.transaction():
            self.assertIsNone(self.repository.command_result(str(uuid.uuid4())))

    # Ordered events and the transactional outbox

    def test_contract_event_sequence_is_dense_and_ordered_per_aggregate(self):
        workflow = self.start(HUMAN)
        self.engine.apply_action(workflow["steps"][0]["id"], command(
            action="start", expected_revision=workflow["revision"], payload={}))
        with self.repository.transaction():
            sequences = [row[0] for row in self.repository.db.execute(
                """SELECT sequence_number FROM event_log
                   WHERE aggregate_type=? AND aggregate_id=? ORDER BY sequence_number""",
                aggregate_args(workflow))]
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))

    def test_contract_every_event_writes_an_outbox_row(self):
        workflow = self.start(HUMAN)
        events = self.scalar(
            "SELECT COUNT(*) FROM event_log WHERE aggregate_type=? AND aggregate_id=?",
            *aggregate_args(workflow))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM outbox_event"), events)

    # Worker claiming

    def test_contract_a_job_is_claimed_by_exactly_one_worker(self):
        self.start(AUTOMATED)
        claims, lock = [], threading.Lock()

        def run(worker):
            jobs = self.engine.claim_automation_jobs(worker)
            with lock:
                claims.extend(job["id"] for job in jobs)

        threads = [threading.Thread(target=run, args=(f"w{i}",)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(claims), 1)

    def test_contract_an_expired_lease_is_reclaimable(self):
        self.start(AUTOMATED)
        first = self.engine.claim_automation_jobs("worker-1")
        self.assertEqual(len(first), 1)
        self.assertEqual(self.engine.claim_automation_jobs("worker-2"), [])
        with self.repository.transaction():
            self.repository.db.execute(
                "UPDATE automation_job SET lease_expires_at=? WHERE id=?",
                ("2000-01-01T00:00:00.000Z", first[0]["id"]))
        self.assertEqual(len(self.engine.claim_automation_jobs("worker-2")), 1)

    def test_contract_suspended_work_is_not_handed_out(self):
        workflow = self.start(AUTOMATED)
        self.engine.apply_lifecycle_action(owner(workflow), command(
            action="suspend", expected_revision=workflow["revision"]))
        self.assertEqual(self.engine.claim_automation_jobs("worker-1"), [])
        self.assertEqual(self.engine.process_due_timers(), 0)

    # Signals

    def test_contract_a_signal_is_consumed_exactly_once(self):
        workflow = self.start(WAITING)
        signal = command(signal_type="READY", payload={})
        first = self.engine.receive_signal(owner(workflow), signal)
        second = self.engine.receive_signal(owner(workflow), signal)
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(
            self.scalar("""SELECT COUNT(*) FROM signal_receipt
                           WHERE owner_type=? AND owner_id=?""", *owner(workflow)),
            1)

    # Facts

    def test_contract_fact_history_records_the_previous_and_new_value(self):
        workflow = self.start(HUMAN)
        self.engine.update_facts(owner(workflow), command(
            facts={"tier": "GOLD"}, expected_revision=workflow["revision"],
            source_type="CONTRACT", source_reference="REF-1"))
        updated = self.engine.get_aggregate(owner(workflow))
        self.engine.update_facts(owner(workflow), command(
            facts={"tier": "PLATINUM"}, expected_revision=updated["revision"]))
        with self.repository.transaction():
            rows = [dict(row) for row in self.repository.db.execute(
                """SELECT previous_value_json,new_value_json FROM workflow_fact_history
                   WHERE owner_type=? AND owner_id=? AND fact_key='tier' ORDER BY id""",
                owner(workflow))]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["previous_value_json"], "null")
        self.assertEqual(rows[1]["previous_value_json"], '"GOLD"')

    # Assignment eligibility

    def test_contract_candidate_eligibility_is_organization_aware(self):
        workflow = self.start(HUMAN)
        step_id = workflow["steps"][0]["id"]
        with self.repository.transaction():
            self.assertFalse(self.repository.candidate_allowed(step_id, {
                "actor_id": "mallory", "organization_id": "ORG-B", "groups": ["REVIEWERS"]}))
            self.assertTrue(self.repository.candidate_allowed(step_id, {
                "actor_id": "alice", "organization_id": "ORG-A", "groups": ["REVIEWERS"]}))

    # Queries

    def test_contract_work_queries_filter_and_paginate(self):
        for _ in range(3):
            self.start(HUMAN)
        page = self.repository.list_work(limit=2, offset=0)
        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["items"]), 2)
        self.assertEqual(len(self.repository.list_work(limit=2, offset=2)["items"]), 1)

    def test_contract_operational_counters_are_reported(self):
        self.start(HUMAN)
        counters = self.repository.operational_counters()
        for key in ("aggregates_running", "jobs_queued", "outbox_pending",
                    "outbox_dead_letter", "timers_due", "aggregates_stuck"):
            self.assertIn(key, counters)
        self.assertEqual(counters["aggregates_running"], 1)

    # The shared reliability surface

    def test_contract_domain_and_execution_events_share_one_sequence(self):
        """The single clearest argument for fusing, asserted.

        A determination recorded by the domain and the step transition it
        caused sit in one stream, in order, on one aggregate. Two logs could
        not have said which came first.
        """
        workflow = self.start(HUMAN)
        before = self.engine.list_events(*aggregate_args(workflow))
        self.assertGreater(len(before), 0, "execution events land on the aggregate")

        self.engine.record_event(*aggregate_args(workflow), "REQUEST_DETERMINED")
        self.engine.apply_action(workflow["steps"][0]["id"], command(
            action="start", expected_revision=workflow["revision"], payload={}))

        events = self.engine.list_events(*aggregate_args(workflow))
        self.assertEqual([item["sequence_number"] for item in events],
                         list(range(1, len(events) + 1)))
        types = [item["event_type"] for item in events]
        self.assertLess(types.index("REQUEST_DETERMINED"), types.index("STEP_START"))

    def test_contract_a_domain_event_is_paired_with_an_outbox_row(self):
        self.start(HUMAN)
        before = self.scalar("SELECT COUNT(*) FROM outbox_event")
        self.engine.record_event("ISRP_FINDING", "F-2", "FINDING_RAISED")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM outbox_event"), before + 1)

    def test_contract_the_inbox_deduplicates_without_an_aggregate(self):
        event = {"connector_name": "contract-connector", "provider_event_id": "p-1",
                 "event_type": "SOMETHING_HAPPENED"}
        self.assertFalse(self.engine.record_inbox_event(event)["duplicate"])
        self.assertTrue(self.engine.record_inbox_event(event)["duplicate"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM inbox_event"), 1)

    # Delivery

    def test_contract_outbox_claim_is_recoverable_and_dead_letters(self):
        self.start(HUMAN)
        claimed = self.repository.pending_outbox(limit=1)
        self.assertEqual(len(claimed), 1)
        outbox_id = claimed[0]["id"]
        with self.repository.transaction():
            self.repository.db.execute(
                "UPDATE outbox_event SET attempts=max_attempts-1 WHERE id=?", (outbox_id,))
        self.repository.mark_outbox(outbox_id, False, "refused")
        self.assertEqual(
            [item["id"] for item in self.repository.dead_letter_events()], [outbox_id])
        self.assertTrue(self.repository.redrive_outbox(outbox_id))
        self.assertEqual(self.repository.dead_letter_events(), [])
