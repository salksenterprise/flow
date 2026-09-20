# Flow

Flow is a configurable workflow execution engine built from a domain-neutral Python core, a SQLite persistence adapter, a FastAPI microservice, a delivery worker, and a React administration console.

The engine executes workflow graphs and FSM-controlled steps. It does not own information-security requirements, assessments, threats, architecture records, findings, or other client-domain data.

## Architecture

```text
Domain applications
    ISR, architecture, threat modeling, vendor review
                        |
                        | commands and events
                        v
Workflow API -> workflow-core -> SQLite adapter
      |               |
      |               +-- graph orchestration
      |               +-- step FSMs
      |               +-- conditions and joins
      v
Transactional outbox -> delivery worker -> webhooks
```

Repository layout:

```text
packages/workflow-core       Pure Python engine and repository ports
packages/workflow-sqlite     SQLite schema and repository adapter
services/workflow-api        FastAPI service adapter
services/workflow-worker     Outbox/webhook delivery process
frontend                     React administration console
examples                     External-domain templates and clients
tests                        Cross-component engine tests
docs                         Integration contract
```

## Run with Docker

Docker builds the React interface inside the image, so Node and npm are not required on the host.

```bash
docker compose up --build
```

Open:

- Console: `http://localhost:8000`
- API documentation: `http://localhost:8000/docs`


If port 8000 is already in use, choose another host port:

```bash
FLOW_PORT=8080 docker compose up --build
```

Then open `http://localhost:8080`.

Stop the services:

```bash
docker compose down
```

Workflow data persists in the `workflow-data` volume. Delete it intentionally with:

```bash
docker compose down --volumes
```

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r services/workflow-api/requirements.txt
pip install -e packages/workflow-core -e packages/workflow-sqlite

export PYTHONPATH="$PWD/packages/workflow-core/src:$PWD/packages/workflow-sqlite/src:$PWD/services/workflow-api:$PWD/services/workflow-worker"
uvicorn app.main:app --app-dir services/workflow-api --reload --port 8000
```

In another terminal, optionally run the delivery worker:

```bash
source .venv/bin/activate
export PYTHONPATH="$PWD/packages/workflow-core/src:$PWD/packages/workflow-sqlite/src:$PWD/services/workflow-api:$PWD/services/workflow-worker"
python -m worker.main
```

Node is needed only for frontend development:

```bash
cd frontend
npm ci
npm run dev
```

## Generic runtime concepts

- Immutable, versioned workflow definitions
- Human, decision, automated, fork, join, milestone, and end steps
- Independent FSM state for every step instance
- Conditional transitions using a constrained JSON rule language
- Parallel branches with `ALL` and `ANY` joins
- Clarification and response cycles inside human work
- Generic subjects and opaque business references
- Role, group, or user assignments
- Optimistic workflow revisions
- Idempotent client commands
- Ordered audit events
- Transactional event outbox and signed webhooks

## Client integration

A client starts a workflow with its own business reference:

```json
{
  "command_id": "client-generated-uuid",
  "workflow_version_id": 1,
  "title": "Review ASMT-502",
  "business_type": "ISR_ASSESSMENT",
  "business_key": "ASMT-502",
  "correlation_id": "ISR-REQ-200",
  "variables": {"identity_review_required": true},
  "subjects": []
}
```

The engine treats all domain values as opaque. See [docs/integration.md](docs/integration.md) and the [ISR example](examples/information-security-review/README.md).

## Rule format

```json
{"field": "specialist_review_required", "operator": "eq", "value": true}
```

Supported operators: `eq`, `ne`, `in`, `not_in`, `exists`, and `truthy`. Compose rules with `all`, `any`, and `not`. Rules are data and cannot execute arbitrary code.

## Tests

```bash
export PYTHONPATH="$PWD/packages/workflow-core/src:$PWD/packages/workflow-sqlite/src:$PWD/services/workflow-api:$PWD/services/workflow-worker"
python -m unittest discover -s tests -v
```
