# ISRP Workflow Visual Guide

## Purpose

This guide explains how FSMs and DAGs work together at the request, assessment, and executable-step levels.

~~~text
FSM = What state is this one thing currently in?

DAG = What work must happen, in what order,
      and what work may happen in parallel?
~~~

## Complete hierarchy

~~~text
+----------------------------------------------------------+
| ISRP REQUEST                                             |
| Example: Introduce SecureCloud                           |
|                                                          |
| Request FSM                                              |
|   Tracks the overall request lifecycle                   |
|                                                          |
| Request DAG                                              |
|   Determines which assessments must run                  |
|                                                          |
|   +--------------------------------------------------+   |
|   | EXTERNAL ASSESSMENT                              |   |
|   |                                                  |   |
|   | Assessment FSM                                   |   |
|   |   Tracks overall assessment lifecycle            |   |
|   |                                                  |   |
|   | Assessment DAG                                   |   |
|   |   Organizes phases, reviews, branches, and joins  |   |
|   |                                                  |   |
|   |   +------------------------------------------+   |   |
|   |   | HUMAN OR AUTOMATION STEP                 |   |   |
|   |   |                                          |   |   |
|   |   | Step FSM                                 |   |   |
|   |   | Assignment, work, clarification,         |   |   |
|   |   | submission, and completion               |   |   |
|   |   +------------------------------------------+   |   |
|   +--------------------------------------------------+   |
|                                                          |
|   +--------------------------------------------------+   |
|   | INFRASTRUCTURE ASSESSMENT                       |   |
|   | Assessment FSM + Assessment DAG + Step FSMs      |   |
|   +--------------------------------------------------+   |
+----------------------------------------------------------+
~~~

## Request FSM

The request FSM answers: What is the overall business state of this request?

~~~text
                    +-----------+
                    |   DRAFT   |
                    +-----+-----+
                          |
                       Submit
                          |
                          v
                    +-----------+
                    | SUBMITTED |
                    +-----+-----+
                          |
                          v
                  +---------------+
                  | CATEGORIZING  |
                  +-------+-------+
                          |
                  Create assessments
                          |
                          v
          +--------------------------------+
          | ASSESSMENTS_IN_PROGRESS        |
          +----------------+---------------+
                           |
                 All required assessments
                 satisfy completion policy
                           |
                           v
                  +----------------+
                  | READY_TO_CLOSE |
                  +--------+-------+
                           |
                    Authorized closure
                           |
                           v
                      +--------+
                      | CLOSED |
                      +--------+
~~~

Exceptional transitions:

~~~text
                         +---------+
Any active state ------> | ON_HOLD |
                         +----+----+
                              |
                            Resume
                              |
                              v
                       Previous active state

Any non-terminal state -> CANCELLED
~~~

The request FSM does not show which assessments are running. That belongs to the request DAG.

## Request DAG

The request DAG answers: Which assessments are required, and when may the request continue?

Example: a SaaS product with an on-premises connector.

~~~text
                         +----------------+
                         | Request Intake |
                         +--------+-------+
                                  |
                                  v
                         +----------------+
                         | Categorization |
                         +--------+-------+
                                  |
                                  v
                      +----------------------+
                      | Determine Scope      |
                      |                      |
                      | External?       Yes  |
                      | On-prem part?   Yes  |
                      +----------+-----------+
                                 |
                    +------------+------------+
                    |                         |
                    v                         v
       +-------------------------+  +-------------------------+
       | External Assessment     |  | Infrastructure          |
       |                         |  | Assessment              |
       | External assessment FSM |  | Infrastructure FSM      |
       | and DAG run separately  |  | and DAG run separately  |
       +------------+------------+  +------------+------------+
                    |                            |
                    +-------------+--------------+
                                  |
                                  v
                      +----------------------+
                      | Required Assessments |
                      | Join                 |
                      +----------+-----------+
                                 |
                   All required assessments
                   satisfy completion policy
                                 |
                                 v
                      +----------------------+
                      | Request Closure      |
                      | Review               |
                      +----------------------+
~~~

Conditional examples:

~~~text
External product without on-prem component:

Categorization
      |
      +--> External Assessment
               |
               v
          Request Join


Infrastructure-only product:

Categorization
      |
      +--> Infrastructure Assessment
               |
               v
          Request Join
~~~

The request DAG can create one or more assessment workflow instances.

## Request FSM and DAG together

~~~text
REQUEST FSM                         REQUEST DAG

CATEGORIZING                        Categorization node running
     |                                      |
     | creates assessments                  |
     v                                      v
ASSESSMENTS_IN_PROGRESS             External Assessment
                                    Infrastructure Assessment
                                             |
                                             v
                                    Required-assessments join
                                             |
     +---------------------------------------+
     |
     v
READY_TO_CLOSE
~~~

The DAG produces events that permit FSM transitions:

~~~text
Request DAG event:
    REQUIRED_ASSESSMENTS_COMPLETED

Request FSM transition:
    ASSESSMENTS_IN_PROGRESS
        -> READY_TO_CLOSE
~~~

The DAG does not directly set the request to CLOSED. Closure requires a separate authorized FSM command.

## Assessment FSM

The assessment FSM answers: What is the overall state of this assessment?

~~~text
                  +---------+
                  |  DRAFT  |
                  +----+----+
                       |
                    Plan
                       |
                       v
                  +---------+
                  | PLANNED |
                  +----+----+
                       |
                    Start
                       |
                       v
                 +-------------+
                 | IN_PROGRESS |
                 +------+------+
                        |
             All required phases,
             requirements, and work
             packages satisfy policy
                        |
                        v
              +-------------------+
              | READY_TO_COMPLETE |
              +---------+---------+
                        |
               Authorized completion
                        |
                        v
                  +-----------+
                  | COMPLETED |
                  +-----------+
~~~

Exceptional transitions:

~~~text
IN_PROGRESS ------> ON_HOLD ------> IN_PROGRESS

DRAFT
PLANNED
IN_PROGRESS ------> CANCELLED
ON_HOLD
~~~

The assessment FSM stays deliberately small. Detailed review states belong to the assessment DAG and step FSMs.

## Assessment DAG

The assessment DAG answers: Which phases and reviews must happen?

~~~text
                       +------------------+
                       | Confirm Scope    |
                       +---------+--------+
                                 |
                                 v
                       +------------------+
                       | Lab Build Needed?|
                       +----+---------+---+
                            |         |
                           Yes        No
                            |         |
                            v         |
                    +---------------+ |
                    | Lab Build     | |
                    | and Evidence  | |
                    +-------+-------+ |
                            |         |
                            +----+----+
                                 |
                                 v
                    +-----------------------+
                    | Design Review         |
                    +-----------+-----------+
                                |
                                v
                       +----------------+
                       | Select Required|
                       | SME Reviews    |
                       +-------+--------+
                               |
             +-----------------+-----------------+
             |                 |                 |
             v                 v                 v
   +----------------+ +----------------+ +----------------+
   | Identity SME   | | Network SME    | | Privacy SME    |
   | Review         | | Review         | | Review         |
   +-------+--------+ +-------+--------+ +-------+--------+
           |                  |                  |
           +------------------+------------------+
                              |
                              v
                  +-------------------------+
                  | SME Review Join         |
                  | Wait for required work  |
                  +------------+------------+
                               |
                               v
                  +-------------------------+
                  | Consolidate Decisions   |
                  +------------+------------+
                               |
                               v
                  +-------------------------+
                  | Build / Implementation  |
                  +------------+------------+
                               |
                               v
                  +-------------------------+
                  | Validate                |
                  +------------+------------+
                               |
                               v
                  +-------------------------+
                  | Assessment Closure      |
                  +-------------------------+
~~~

Different assessment types publish different DAG definitions.

~~~text
External assessment:

Scope
  -> Vendor Evidence Collection
  -> Design Review
       +-> Identity SME
       +-> Privacy SME
       +-> Third-Party Risk
  -> Join
  -> Final Decisions
  -> Closure


Infrastructure assessment:

Scope
  -> Lab Build
  -> Configuration Evidence
  -> Design Review
       +-> Identity SME
       +-> Network SME
       +-> Platform SME
  -> Join
  -> Production Validation
  -> Closure


Internal application assessment:

Scope
  -> Architecture Review
       +-> Authentication Review
       +-> Data Protection Review
       +-> Secure Development Review
       +-> Threat Model Review
  -> Join
  -> Implementation
  -> Security Testing
  -> Closure
~~~

## Human-step FSM

A human-step FSM answers: What is happening with this assigned piece of work?

~~~text
                 +-------------+
                 | NOT_STARTED |
                 +------+------+
                        |
                     Activate
                        |
                        v
                  +-----------+
                  | AVAILABLE |
                  +-----+-----+
                        |
                     Assign
                        |
                        v
                   +----------+
                   | ASSIGNED |
                   +----+-----+
                        |
                      Start
                        |
                        v
                 +-------------+
                 | IN_PROGRESS |
                 +------+------+
                        |
                      Submit
                        |
                        v
                  +-----------+
                  | SUBMITTED |
                  +-----+-----+
                        |
                        v
                  +-----------+
                  | IN_REVIEW |
                  +-----+-----+
                        |
            +-----------+-----------+
            |                       |
          Accept               Need more info
            |                       |
            v                       v
     +-------------+      +-----------------------+
     |  COMPLETED  |      | WAITING_FOR_RESPONSE  |
     +-------------+      +-----------+-----------+
                                      |
                                Response received
                                      |
                                      v
                            +-------------------+
                            | RESPONSE_RECEIVED |
                            +---------+---------+
                                      |
                                      v
                                +-----------+
                                | IN_REVIEW |
                                +-----------+
~~~

The clarification loop may repeat without changing the assessment DAG:

~~~text
IN_REVIEW
  -> WAITING_FOR_RESPONSE
  -> RESPONSE_RECEIVED
  -> IN_REVIEW
  -> WAITING_FOR_RESPONSE
  -> RESPONSE_RECEIVED
  -> IN_REVIEW
  -> COMPLETED
~~~

## Deterministic automation-step FSM

Current automation is deterministic and non-AI.

~~~text
               +--------+
               | QUEUED |
               +---+----+
                   |
                   v
              +---------+
              | RUNNING |
              +----+----+
                   |
          +--------+---------+
          |                  |
        Success            Failure
          |                  |
          v                  v
    +-----------+      +------------+
    | SUCCEEDED |      | RETRY_WAIT |
    +-----------+      +------+-----+
                              |
                            Retry
                              |
                              v
                         +---------+
                         | RUNNING |
                         +----+----+
                              |
                       Retry limit reached
                              |
                              v
                    +----------------------+
                    | MANUAL_REVIEW_NEEDED |
                    +----------------------+
~~~

Examples include malware scan, evidence hashing, duplicate checking, requirement-set resolution, work-package creation, status projection, issue publication, and notification delivery.

## DAG node and step FSM relationship

An executable DAG node owns a step instance.

~~~text
ASSESSMENT DAG NODE

+----------------------+
| Identity SME Review  |
+----------+-----------+
           |
           | owns
           v

STEP INSTANCE FSM

NOT_STARTED
  -> AVAILABLE
  -> ASSIGNED
  -> IN_PROGRESS
  -> SUBMITTED
  -> IN_REVIEW
  -> COMPLETED
~~~

While the step FSM is not terminal:

~~~text
Identity SME Review DAG node = ACTIVE
~~~

When the step FSM reaches COMPLETED:

~~~text
Identity SME Review DAG node = COMPLETED
~~~

The downstream join can then reevaluate.

## Parallel reviews and join

~~~text
                       Design Review
                             |
             +---------------+---------------+
             |               |               |
             v               v               v
       Identity SME     Network SME      Privacy SME
       Step FSM         Step FSM         Step FSM

       COMPLETED        IN_REVIEW        COMPLETED
             |               |               |
             +---------------+---------------+
                             |
                             v
                       SME Review Join
~~~

For an ALL join:

~~~text
Identity: COMPLETED
Network:  IN_REVIEW
Privacy:  COMPLETED

Join: WAITING
~~~

Later:

~~~text
Identity: COMPLETED
Network:  COMPLETED
Privacy:  COMPLETED

Join: SATISFIED
~~~

The next DAG node then activates.

## Requirement work inside a step

A review step can own a work package containing selected requirements.

~~~text
Identity SME Review Step
    |
    v
Identity Work Package
    |
    +-- ADS-001
    +-- ADS-002
    +-- ADS-007
    +-- ADS-012
~~~

Each assessment requirement retains its own business state:

~~~text
ADS-001: DECIDED
ADS-002: CLARIFICATION_REQUIRED
ADS-007: IN_REVIEW
ADS-012: DECIDED
~~~

The enclosing step may therefore be WAITING_FOR_RESPONSE because a required item needs clarification.

## One moment in a running request

~~~text
REQUEST ISR-100
|
| Request FSM:
|   ASSESSMENTS_IN_PROGRESS
|
| Request DAG:
|   +-- External Assessment ASMT-1
|   +-- Infrastructure Assessment ASMT-2
|   +-- Required Assessment Join: WAITING
|
+----------------------------------------------------+
|                                                    |
v                                                    v

EXTERNAL ASSESSMENT ASMT-1               INFRA ASSESSMENT ASMT-2

Assessment FSM:                          Assessment FSM:
IN_PROGRESS                              IN_PROGRESS

Assessment DAG:                          Assessment DAG:
Design Review                            Validation
  |                                       |
  +-- Identity SME: COMPLETED              +-- Platform Test: RUNNING
  +-- Privacy SME: IN_REVIEW
  +-- Vendor Response: WAITING

Identity step FSM: COMPLETED             Platform automation FSM:
Privacy step FSM: IN_REVIEW              RUNNING
Vendor step FSM:
WAITING_FOR_RESPONSE
~~~

The request projection may show:

~~~text
Lifecycle: ASSESSMENTS_IN_PROGRESS
Attention: ACTION_REQUIRED

Assessments:
    0 of 2 completed

Reason:
    External assessment is waiting for vendor response

Active phases:
    External: Design Review
    Infrastructure: Validation
~~~

The request lifecycle does not become WAITING_FOR_RESPONSE. That is an inner step state surfaced through the separate attention summary.

## When review uncovers a requirement gap

~~~text
REVIEWER AND REQUESTOR CLARIFICATION LOOP
                  |
                  v
        Requirement is not met
                  |
                  v
        +---------------------+
        | Create FINDING      |
        | under ASSESSMENT    |
        +----------+----------+
                   |
             Decide treatment
                   |
       +-----------+------------+----------------+
       |                        |                |
       v                        v                v
+---------------+    +--------------------+  +----------------+
| Fix during    |    | Register external  |  | Risk exception |
| assessment    |    | noncompliance      |  | path           |
+-------+-------+    +---------+----------+  +--------+-------+
        |                      |                      |
        v                      v                      v
Change solution        REMEDIATION_CASE        Governed approval
and submit evidence       |       |             and expiration
        |                 |       |
        v                 v       v
Reviewer validates   ISSUE_REF   CAP
        |                 |       |
        +-----------------+-------+
                          |
                          v
                  ISRP validation
                          |
                          v
                 Resolve/close finding
~~~

The external system does not own the ISRP finding. It owns its issue record. ISRP owns the compliance gap, its requirement links, and the validation needed to close it.

## Where each record belongs

~~~text
ISRP_REQUEST
    |
    +----< ISRP_ASSESSMENT
              |
              +----< ASSESSMENT_REQUIREMENT
              |
              +----< FINDING
                       |
                       +----< FINDING_REQUIREMENT
                       |
                       +----< FINDING_SUBJECT
                       |
                       +----< REMEDIATION_CASE_FINDING
                                  |
                                  v
                           REMEDIATION_CASE
                              |         |
                              |         +----< ISSUE_REFERENCE
                              |
                              +----< CORRECTIVE_ACTION_PLAN
                                          |
                                          +----< CAP_ACTION_ITEM
~~~

Interpretation:

~~~text
Request
  Business container; remediation totals are derived.

Assessment
  Owns the finding because this review discovered the gap.

Finding
  Describes the security gap and links affected requirements/subjects.

Remediation case
  Coordinates treatment; may group findings across assessments.

External issue reference
  Points to the authoritative issue-management record.

Corrective action plan
  Describes corrective work, owners, dates, actions, and evidence.
~~~

## External issue round trip

~~~text
ISRP                                      ISSUE MANAGEMENT

Finding disposition
REGISTER_NONCOMPLIANCE
        |
        v
REMEDIATION_CASE
        |
        v
OUTBOX_EVENT
NONCOMPLIANCE_REGISTRATION_REQUESTED
        | -------------------------------------> Create/find issue
        |                                        idempotently
        | <------------------------------------- Issue ID + status
        v
ISSUE_REFERENCE

Later provider event
        | <------------------------------------- Status = RESOLVED
        v
Shared INBOX_EVENT
(deduplicate and correlate)
        |
        v
REMEDIATION_CASE = VALIDATION_PENDING
        |
        v
Create ISRP validation work
        |
        v
Authorized reviewer validates
        |
        +---- success ----> FINDING = RESOLVED/CLOSED
        |
        +---- failure ----> remediation continues
~~~

External RESOLVED means "ready for ISRP validation," not "finding closed."

## Database view

~~~text
ISRP_REQUEST
    |
    | request_id
    |
    +----< ISRP_ASSESSMENT
              |
              | assessment_id
              |
              +----< WORKFLOW_INSTANCE
              |         |
              |         +----< STEP_INSTANCE
              |                    |
              |                    +---- Step FSM state
              |
              +----< ASSESSMENT_REQUIREMENT
              |
              +----< REQUIREMENT_WORK_PACKAGE
              |
              +----< FINDING
                       |
                       +----< REMEDIATION_CASE_FINDING
                                  |
                                  +----> REMEDIATION_CASE
                                            +----< ISSUE_REFERENCE
                                            +----< CORRECTIVE_ACTION_PLAN
~~~

Status propagation:

~~~text
STEP_INSTANCE or domain record changes
        |
        v
OUTBOX_EVENT
        |
        v
ASSESSMENT_STATUS_PROJECTION
        |
        v
REQUEST_STATUS_PROJECTION
~~~

## Compact mental model

~~~text
REQUEST FSM
    Where is the overall request?

REQUEST DAG
    Which assessments must run?

ASSESSMENT FSM
    Where is this assessment overall?

ASSESSMENT DAG
    Which phases and reviews must run?

STEP FSM
    What is happening with this assigned human
    or deterministic automation work?
~~~

Or, in one view:

~~~text
Request
    FSM = overall request state
    DAG = assessment orchestration

Assessment
    FSM = overall assessment state
    DAG = phase and review orchestration

Step
    FSM = detailed human or automation interaction
~~~
