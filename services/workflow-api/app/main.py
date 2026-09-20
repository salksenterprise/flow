from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from workflow_core import ConflictError, NotFoundError, ValidationError, WorkflowEngine
from workflow_sqlite import SQLiteWorkflowRepository

from .config import DATABASE_PATH, EXAMPLES_PATH, FRONTEND_DIST
from .schemas import StepAction, TemplateImport, WebhookSubscriptionIn, WorkflowStart
from .template_loader import load_examples


repository = SQLiteWorkflowRepository(DATABASE_PATH)
engine = WorkflowEngine(repository)

app = FastAPI(title="Generic Workflow Service", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    repository.initialize()
    load_examples(engine, EXAMPLES_PATH)


def invoke(operation):
    try:
        return operation()
    except NotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ConflictError as error:
        raise HTTPException(409, str(error)) from error
    except (ValidationError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


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
def create_workflow(body: WorkflowStart):
    return invoke(lambda: engine.start_workflow(body.model_dump()))


@app.get("/api/workflows/{workflow_id}")
def workflow(workflow_id: int):
    return invoke(lambda: engine.get_workflow(workflow_id))


@app.post("/api/steps/{step_id}/actions")
def step_action(step_id: int, body: StepAction):
    return invoke(lambda: engine.apply_action(step_id, body.model_dump()))


@app.get("/api/webhook-subscriptions")
def webhook_subscriptions():
    return repository.list_subscriptions()


@app.post("/api/webhook-subscriptions", status_code=201)
def create_webhook_subscription(body: WebhookSubscriptionIn):
    return repository.create_subscription(body.model_dump())


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

