import tempfile
import unittest
from pathlib import Path

from app.db import connect, init_db
from app.engine import apply_action, get_workflow, start_workflow
from app.seed import SECURITY_REVIEW, seed


class WorkflowEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.db"
        init_db(self.path)
        with connect(self.path) as db:
            seed(db)

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, identity=True, network=True):
        with connect(self.path) as db:
            version_id = db.execute(
                """SELECT wv.id FROM workflow_version wv JOIN workflow_definition wd
                ON wd.id=wv.definition_id WHERE wd.key=?""", (SECURITY_REVIEW["key"],)
            ).fetchone()[0]
            return start_workflow(db, {
                "workflow_version_id": version_id,
                "title": "Test review",
                "created_by": "tester",
                "input": {"identity_review": identity, "network_review": network},
                "subjects": [{"subject_type": "APPLICATION", "subject_id": "APP-1", "source_system": "test"}],
            })

    def complete_ready(self, workflow_id, key):
        with connect(self.path) as db:
            step = db.execute(
                """SELECT si.id, si.state FROM step_instance si JOIN step_definition sd
                ON sd.id=si.step_definition_id WHERE si.workflow_instance_id=? AND sd.step_key=?""",
                (workflow_id, key),
            ).fetchone()
            if step["state"] == "READY":
                apply_action(db, step["id"], "start", "tester", {})
            apply_action(db, step["id"], "complete", "tester", {"result": "ok"})

    def test_parallel_smes_join_and_finish(self):
        workflow_id = self.start()
        for key in ("intake", "categorize", "design"):
            self.complete_ready(workflow_id, key)
        with connect(self.path) as db:
            workflow = get_workflow(db, workflow_id)
            states = {step["step_key"]: step["state"] for step in workflow["steps"]}
            self.assertEqual(states["identity_sme"], "READY")
            self.assertEqual(states["network_sme"], "READY")
            self.assertEqual(states["architecture_sme"], "READY")
            self.assertEqual(states["join_sme"], "NOT_READY")
        for key in ("identity_sme", "network_sme", "architecture_sme", "consolidate", "build", "validate"):
            self.complete_ready(workflow_id, key)
        with connect(self.path) as db:
            workflow = get_workflow(db, workflow_id)
            self.assertEqual(workflow["status"], "COMPLETED")

    def test_condition_skips_unneeded_sme_branch(self):
        workflow_id = self.start(identity=False, network=True)
        for key in ("intake", "categorize", "design"):
            self.complete_ready(workflow_id, key)
        with connect(self.path) as db:
            states = {step["step_key"]: step["state"] for step in get_workflow(db, workflow_id)["steps"]}
            self.assertEqual(states["identity_sme"], "NOT_READY")
            self.assertEqual(states["network_sme"], "READY")
            self.assertEqual(states["architecture_sme"], "READY")

    def test_clarification_cycle(self):
        workflow_id = self.start()
        with connect(self.path) as db:
            step = db.execute(
                """SELECT si.id FROM step_instance si JOIN step_definition sd
                ON sd.id=si.step_definition_id
                WHERE si.workflow_instance_id=? AND sd.step_key='intake'""",
                (workflow_id,),
            ).fetchone()[0]
            apply_action(db, step, "start", "reviewer", {})
            apply_action(db, step, "request_clarification", "reviewer", {"question": "Provide evidence"})
            apply_action(db, step, "respond", "requestor", {"answer": "Attached"})
            apply_action(db, step, "resume", "reviewer", {})
            apply_action(db, step, "complete", "reviewer", {})
            history = db.execute("SELECT event_type FROM workflow_event WHERE step_instance_id=?", (step,)).fetchall()
            self.assertEqual(len(history), 6)  # activation plus five actions


if __name__ == "__main__":
    unittest.main()
