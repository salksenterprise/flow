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
isrp/templates         published workflow definitions
tests                  orchestration tests and the adapter contract suite
docs/isrp              the design
~~~

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

Several documents under `docs/` describe an earlier architecture in which
orchestration was a separate, domain-neutral product. They are marked superseded
and are retained only until their content has been folded into the design set.
