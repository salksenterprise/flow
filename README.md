# ISRP

The Information Security Review Process: intake, categorization, assessment
execution, requirement review, evidence, findings, remediation and closure.

ISRP orchestrates its own work. The state machines, dependency graphs, durable
timers, work assignment and reliability plumbing are ISRP code, in this
repository, using ISRP's types. There is no separate workflow engine, service
or package.

## Layout

~~~text
isrp/orchestration     state machines, the graph driver, timers, jobs,
                       signals, work assignment, the shared event log
isrp/application.py    transactional ISRP use cases
isrp/api.py            JSON HTTP adapter and compiled-frontend host
isrp/templates         published workflow definitions
frontend               React/Vite ISRP workspace
tests                  orchestration tests and the adapter contract suite
docs/isrp              the design
~~~

## Run the application

Install and build the frontend once:

~~~bash
cd frontend
npm install
npm run build
cd ..
~~~

Start the fused ISRP application:

~~~bash
PYTHONPATH=. python3 -m isrp.api --database isrp.db
~~~

Open `http://127.0.0.1:8000`. The same process serves the JSON API and compiled
React application. For frontend development, run `npm run dev` in `frontend/`;
Vite proxies `/api` to the Python process on port 8000.

This first slice uses a process-configured development identity
(`ISRP_DEVELOPMENT_ACTOR`) and does not trust identity headers from the browser.
Production authentication and authorization remain an enterprise-control
delivery item; do not expose this development server to an untrusted network.

## Running the tests

~~~bash
PYTHONPATH=.:tests python3 -m unittest discover -s tests
~~~

No container, no network, no background process. That is deliberate: a test
that needs infrastructure is a test nobody runs.

## Databases

SQLite is the development and test adapter. Oracle is the production store and
is not yet implemented. `tests/contract.py` is what certifies an adapter; an
Oracle store is a subclass of that suite, not a second copy of it.

No release may claim Oracle support until the Oracle adapter passes that suite
against a real Oracle instance.

## Design

Start with [the ISRP design set](docs/isrp/README.md), and
[Orchestration](docs/isrp/orchestration.md) for how execution works.

The historical Releases 1-3 record under `docs/` describes the retired
standalone engine. The current architecture is defined only by the ISRP design
set.
