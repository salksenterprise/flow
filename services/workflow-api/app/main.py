from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from workflow_core import (
    ConflictError, ExecutionError, NotFoundError, ValidationError, WorkflowEngine,
)
from workflow_sqlite import SQLiteWorkflowRepository

from .config import DATABASE_PATH, EXAMPLES_PATH, FRONTEND_DIST
from .schemas import (
    AutomationResult,
    ExternalEventIn,
    FactUpdate,
    SignalIn,
    RepairAction,
    StepAction,
    TemplateImport,
    VersionMigration,
    WebhookSubscriptionIn,
    WorkflowAction,
    WorkflowStart,
)
from .security import ACTOR_HEADER, actor_from_header
from .template_loader import load_examples


@asynccontextmanager
async def lifespan(_: FastAPI):
    repository.initialize()
    load_examples(engine, EXAMPLES_PATH)
    yield

repository = SQLiteWorkflowRepository(DATABASE_PATH)
engine = WorkflowEngine(repository)

app = FastAPI(title="Flow Workflow Service", version="0.4.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



def invoke(operation):
    try:
        return operation()
    except NotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ConflictError as error:
        raise HTTPException(409, str(error)) from error
    except (ValidationError, ValueError) as error:
        raise HTTPException(400, str(error)) from error
    except ExecutionError as error:
        raise HTTPException(500, str(error)) from error


def commanded(body, request: Request) -> dict:
    """Body plus a trusted actor.

    The resolved actor replaces whatever the client sent, so a caller cannot
    assert its own permissions by putting them in the request.
    """
    data = body.model_dump()
    try:
        data["actor"] = actor_from_header(request.headers.get(ACTOR_HEADER))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return data


@app.get("/api/health")
def health():
    return {"status": "ok", "component": "workflow-api"}


@app.get("/api/templates")
def templates():
    return engine.list_templates()


@app.get("/api/templates/{version_id}")
def template(version_id: int):
    return invoke(lambda: engine.get_template(version_id))


@app.post("/api/templates/import", status_code=201)
def create_template(body: TemplateImport):
    return invoke(lambda: engine.import_template(body.model_dump()))


@app.get("/api/workflows")
def workflows():
    return engine.list_workflows()


@app.post("/api/workflows", status_code=201)
def create_workflow(body: WorkflowStart, request: Request):
    return invoke(lambda: engine.start_workflow(commanded(body, request)))


@app.get("/api/workflows/{workflow_id}")
def workflow(workflow_id: int):
    return invoke(lambda: engine.get_workflow(workflow_id))


@app.post("/api/workflows/{workflow_id}/children", status_code=201)
def create_child_workflow(workflow_id: int, body: WorkflowStart, request: Request):
    return invoke(lambda: engine.start_child_workflow(workflow_id, commanded(body, request)))


@app.post("/api/workflows/{workflow_id}/actions")
def workflow_action(workflow_id: int, body: WorkflowAction, request: Request):
    return invoke(lambda: engine.apply_workflow_action(workflow_id, commanded(body, request)))


@app.post("/api/workflows/{workflow_id}/facts")
def update_workflow_facts(workflow_id: int, body: FactUpdate, request: Request):
    return invoke(lambda: engine.update_facts(workflow_id, commanded(body, request)))


@app.post("/api/workflows/{workflow_id}/signals")
def signal_workflow(workflow_id: int, body: SignalIn, request: Request):
    return invoke(lambda: engine.receive_signal(workflow_id, commanded(body, request)))


@app.post("/api/workflows/{workflow_id}/external-events")
def external_event(workflow_id: int, body: ExternalEventIn):
    return invoke(lambda: engine.ingest_external_event(workflow_id, body.model_dump()))


@app.post("/api/steps/{step_id}/actions")
def step_action(step_id: int, body: StepAction, request: Request):
    return invoke(lambda: engine.apply_action(step_id, commanded(body, request)))


@app.get("/api/automation/jobs")
def claim_automation_jobs(worker_id: str, limit: int = 10):
    return engine.claim_automation_jobs(worker_id, limit)


@app.post("/api/automation/jobs/{job_id}/result")
def automation_result(job_id: int, body: AutomationResult, request: Request):
    return invoke(lambda: engine.complete_automation_job(job_id, commanded(body, request)))


@app.post("/api/timers/process")
def process_timers():
    return {"processed": engine.process_due_timers()}


@app.post("/api/workflows/{workflow_id}/migrate-version")
def migrate_version(workflow_id: int, body: VersionMigration, request: Request):
    return invoke(lambda: engine.migrate_workflow_version(
        workflow_id, commanded(body, request)))


@app.get("/api/work")
def work_queue(request: Request, assignee: str | None = None, mine: bool = False,
               execution_status: str | None = None, step_type: str | None = None,
               business_type: str | None = None, business_key: str | None = None,
               workflow_id: int | None = None, limit: int = 50, offset: int = 0):
    """Open work. `mine=true` uses the caller's identity and candidate rules."""
    actor = actor_from_header(request.headers.get(ACTOR_HEADER)) if mine else None
    return invoke(lambda: engine.list_work(
        assignee=assignee, actor=actor, execution_status=execution_status,
        step_type=step_type, business_type=business_type, business_key=business_key,
        workflow_id=workflow_id, limit=min(limit, 200), offset=offset))


@app.post("/api/steps/{step_id}/repair")
def repair_step(step_id: int, body: RepairAction, request: Request):
    return invoke(lambda: engine.repair_step(step_id, commanded(body, request)))


@app.get("/api/operations/counters")
def operational_counters():
    return engine.operational_counters()


@app.get("/api/operations/stuck")
def stuck_workflows(limit: int = 50):
    return engine.stuck_workflows(limit)


@app.get("/api/operations/dead-letters")
def dead_letters(limit: int = 50):
    return engine.dead_letter_events(limit)


@app.post("/api/operations/dead-letters/{outbox_id}/redrive")
def redrive(outbox_id: int):
    if not engine.redrive_event(outbox_id):
        raise HTTPException(404, "No dead-lettered event with that id")
    return {"redriven": outbox_id}


@app.get("/api/webhook-subscriptions")
def webhook_subscriptions():
    return repository.list_subscriptions()


@app.post("/api/webhook-subscriptions", status_code=201)
def create_webhook_subscription(body: WebhookSubscriptionIn):
    return repository.create_subscription(body.model_dump())


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
