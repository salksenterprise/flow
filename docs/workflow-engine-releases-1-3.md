# Workflow Engine Releases 1-3

> **Historical.** This records what the Releases 1-3 delivery contained, when
> Flow was a standalone service. Several behaviours described here have since
> changed, and the standalone and embedded-engine approaches were both retired
> when orchestration was fused into ISRP. For the current design, see the
> [ISRP design set](isrp/README.md) and [ISRP orchestration](isrp/orchestration.md).


## Scope

This delivery evolves Flow into a domain-neutral orchestration service capable of supporting the ISRP execution structure without storing ISRP requirements, evidence, findings, remediation cases, or CAPs.

## Release 1: Core execution model

Implemented:

- separate workflow lifecycle, workflow execution, node FSM, and node execution state
- normalized, immutable FSM definitions, versions, states, and transitions
- template-specific lifecycle and step FSMs
- built-in backwards-compatible workflow and human-step FSMs
- transition guards, required permissions, and required reasons
- expanded node types and join policies
- DAG cycle detection, reachability, end-path, duplicate-edge, signal, timer, and FSM validation
- published definition version pinning

## Release 2: ISRP orchestration essentials

Implemented:

- parent, root, and child workflow relationships
- required and optional child workflows
- SUBWORKFLOW nodes and child-completion joins
- durable early or late signals
- WAIT_SIGNAL nodes
- controlled fact updates with previous/new values, source, actor, and revision
- candidate users, roles, groups, and organizations
- organization-aware claim eligibility
- assignment replacement history
- step attempts and iteration numbers
- actor organization and permissions in execution events

An ISRP request can now orchestrate external, infrastructure, and internal assessment workflows as opaque child business references.

## Release 3: Durable execution

Implemented:

- queued asynchronous automation jobs
- job claim leases, bounded attempts, retry wait, success, and failure
- durable TIMER nodes
- workflow suspend, resume, terminate, cancel, complete, and configured lifecycle actions
- connector inbox deduplication
- signal translation through the inbox
- outbox claims, stale-claim recovery, exponential retry scheduling, and dead-letter status
- existing signed webhook delivery
- additive migration from the 0.2 SQLite schema

## API additions

~~~text
POST /api/workflows/{id}/children
POST /api/workflows/{id}/actions
POST /api/workflows/{id}/facts
POST /api/workflows/{id}/signals
POST /api/workflows/{id}/external-events

GET  /api/automation/jobs
POST /api/automation/jobs/{id}/result

POST /api/timers/process
~~~

## Verification

The conformance suite covers:

- legacy assessment templates
- configurable workflow and step FSMs
- graph validation
- request/assessment parent-child execution
- signals and connector deduplication
- fact-driven conditional review branches
- organization-aware assignments
- automation claim and completion
- timers
- suspend and resume
- command and signal idempotency
- audit/outbox creation

## Persistence roadmap

SQLite remains the development adapter. Oracle is the next production adapter. PostgreSQL follows Oracle using the same repository contract and conformance suite.
