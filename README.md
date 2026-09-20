# Configurable Review Workflow Engine

A runnable MVP for configurable review workflows using FastAPI, SQLite, and React.

The engine keeps workflow mechanics generic. Information Security Review, Security Architecture Review, and Threat Model Assessment are supplied as template data rather than hard-coded processes.

## Features

- Versioned workflow definitions
- Configurable step and transition graph
- FSM-controlled human, decision, fork, join, milestone, and end steps
- Conditional transitions using a small declarative rule format
- Parallel step execution and `ALL` / `ANY` joins
- Request/reviewer clarification loops within a step FSM
- User and team assignments
- Generic workflow subjects such as applications and technologies
- Append-only workflow event log
- Seeded Information Security Review and Threat Model templates
- React screens for templates, launching workflows, running steps, and viewing history

## Run with Docker

From the extracted `workflow-engine` directory:

```bash
docker compose up --build
```

Open `http://localhost:8000`. Stop it with:

```bash
docker compose down
```

Workflow data persists in the `workflow-data` Docker volume. To stop the app and intentionally delete its database:

```bash
docker compose down --volumes
```

Docker builds the React interface inside the image, so Node and npm do not need to be installed on the host.

## Run without Docker, Node, or npm

The package includes a prebuilt React interface served by FastAPI:

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000`. The API documentation is at `http://localhost:8000/docs`.

## Frontend development

Node and npm are needed only when changing the React source. In that case, start the backend as above and run:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api` calls to the backend.

The SQLite database is created as `backend/workflow.db`. Override it with `WORKFLOW_DB_PATH`.

## Step FSM

```text
NOT_READY -> READY -> ASSIGNED -> IN_PROGRESS -> COMPLETED
                                  |       ^
                                  v       |
                         CLARIFICATION_REQUIRED
                                  |
                         AWAITING_RESPONSE
                                  |
                          RESPONSE_RECEIVED

Terminal alternatives: SKIPPED, FAILED, CANCELLED
```

System steps (`FORK`, `JOIN`, `MILESTONE`, and `END`) are driven automatically. A join activates when its configured `ALL` or `ANY` completion rule is satisfied.

## Condition format

Transitions may contain a JSON condition evaluated against workflow input data:

```json
{"field": "local_component", "op": "eq", "value": true}
```

Supported operators: `eq`, `ne`, `in`, `not_in`, `exists`, and `truthy`. Conditions may be combined with `all`, `any`, and `not`.

## Tests

```bash
cd backend
python -m unittest discover -s tests -v
```
