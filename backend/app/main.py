from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .db import as_dict, connect, init_db
from .engine import apply_action, get_workflow, import_template, start_workflow
from .schemas import StepAction, TemplateImport, WorkflowStart
from .seed import seed


app = FastAPI(title="Configurable Review Workflow Engine", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()
    with connect() as db:
        seed(db)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/templates")
def templates():
    with connect() as db:
        return [as_dict(row) for row in db.execute(
            """SELECT wd.*, wv.id AS workflow_version_id, wv.version_number, wv.status AS version_status,
            (SELECT COUNT(*) FROM step_definition sd WHERE sd.workflow_version_id=wv.id) AS step_count
            FROM workflow_definition wd JOIN workflow_version wv ON wv.definition_id=wd.id
            ORDER BY wd.name, wv.version_number DESC"""
        )]


@app.get("/api/templates/{version_id}")
def template(version_id: int):
    with connect() as db:
        version = as_dict(db.execute(
            """SELECT wv.*, wd.key, wd.name, wd.description, wd.domain
            FROM workflow_version wv JOIN workflow_definition wd ON wd.id=wv.definition_id WHERE wv.id=?""",
            (version_id,),
        ).fetchone())
        if not version:
            raise HTTPException(404, "Template version not found")
        version["steps"] = [as_dict(row) for row in db.execute("SELECT * FROM step_definition WHERE workflow_version_id=? ORDER BY id", (version_id,))]
        version["transitions"] = [as_dict(row) for row in db.execute(
            """SELECT td.*, a.step_key AS from_step, b.step_key AS to_step
            FROM transition_definition td JOIN step_definition a ON a.id=td.from_step_id
            JOIN step_definition b ON b.id=td.to_step_id WHERE td.workflow_version_id=? ORDER BY td.priority, td.id""", (version_id,)
        )]
        return version


@app.post("/api/templates/import", status_code=201)
def create_template(body: TemplateImport):
    try:
        with connect() as db:
            return import_template(db, body.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.get("/api/workflows")
def workflows():
    with connect() as db:
        return [as_dict(row) for row in db.execute(
            """SELECT wi.*, wd.name AS workflow_name FROM workflow_instance wi
            JOIN workflow_version wv ON wv.id=wi.workflow_version_id
            JOIN workflow_definition wd ON wd.id=wv.definition_id ORDER BY wi.id DESC"""
        )]


@app.post("/api/workflows", status_code=201)
def create_workflow(body: WorkflowStart):
    try:
        with connect() as db:
            workflow_id = start_workflow(db, body.model_dump())
            return get_workflow(db, workflow_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/workflows/{workflow_id}")
def workflow(workflow_id: int):
    with connect() as db:
        result = get_workflow(db, workflow_id)
        if not result:
            raise HTTPException(404, "Workflow not found")
        return result


@app.post("/api/steps/{step_id}/actions")
def step_action(step_id: int, body: StepAction):
    try:
        with connect() as db:
            apply_action(db, step_id, body.action, body.actor, body.payload, body.assignee, body.assignee_type)
            workflow_id = db.execute("SELECT workflow_instance_id FROM step_instance WHERE id=?", (step_id,)).fetchone()[0]
            return get_workflow(db, workflow_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


# Keep this mount after every API route so /api always reaches FastAPI first.
frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
