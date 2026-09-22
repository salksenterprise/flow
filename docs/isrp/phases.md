# Current and Future ISRP Phases

> Canonical model: All entity definitions, fields, relationships, constraints, and ownership decisions are consolidated in [Data model](data-model.md). This document describes behavior and uses abbreviated entity views only.

## Purpose

This document establishes a firm delivery boundary between the current deterministic ISRP implementation and future AI-assisted capabilities.

## Governing decision

The current phase is purely deterministic and non-AI.

~~~text
Current:
  HUMAN
  AUTOMATION

Future:
  AI_ASSISTED_HUMAN
  AI_AUTOMATED_SUPERVISED
~~~

Flow has no execution-mode concept: it knows node types, not who or what
performs the work. Execution mode is an ISRP field, and ISRP validates its own
templates before importing them, because Flow's publication validation cannot
reject a mode it does not model. AI is introduced only through separately
published future workflow versions. See the open dependencies in
[Architecture](architecture.md).

## Capability summary

| Capability | Current | Future |
|---|---:|---:|
| Human request and assessment intake | Yes | Yes |
| Higher-level evidence upload | Yes | Yes |
| Immutable evidence versioning | Yes | Yes |
| File validation, hashing, and malware scan | Yes | Yes |
| Exact duplicate detection | Yes | Yes |
| Mechanically supported text extraction | Yes | Yes |
| Rule-based categorization | Yes | Yes |
| Rule-based requirement-set selection | Yes | Yes |
| Rule-based metadata augmentation | Yes | Yes |
| Human-created evidence citations | Yes | Yes |
| Human responder assertions | Yes | Yes |
| Human SME determinations | Yes | Yes |
| Human final decisions | Yes | Yes |
| RDBMS status projections | Yes | Yes |
| Outbox processing and integrations | Yes | Yes |
| AI metadata inference | No | Yes |
| AI applicability proposals | No | Yes |
| AI response prefilling | No | Yes |
| AI citation proposals | No | Yes |
| AI evidence summarization | No | Yes |
| AI missing-evidence suggestions | No | Yes |
| AI potential-finding proposals | No | Yes |

## Current deterministic flow

~~~text
Human intake
  -> deterministic evidence processing
  -> deterministic categorization
  -> deterministic requirement-set resolution
  -> deterministic work-package creation
  -> human response and citations
  -> human SME review and clarification
  -> human final decision
  -> deterministic finding disposition, remediation/CAP/issue integration, validation, joins, projections, and closure checks
~~~

## Current human responsibilities

Authorized humans:

- enter and confirm structured intake metadata
- classify uploaded evidence
- determine requirement applicability where rules are insufficient
- author or approve responses
- create and verify evidence citations
- create responder assertions
- record SME determinations
- issue final decisions
- accept exceptions according to policy
- authorize request and assessment closure

Automation cannot substitute for these authorities.

## Current deterministic automation

Allowed automation includes:

- file type and size validation
- malware scanning
- content hashing
- immutable file storage and versioning
- exact duplicate detection
- native-text extraction and page/section indexing
- structured source-system metadata lookup
- explicit field mapping
- versioned rule evaluation
- requirement-set and overlay resolution
- work-package creation and assignment routing
- due-date and SLA calculation
- required-field and citation validation
- reminders, escalations, retries, and timers
- status projection and roll-up
- outbox publication and issue integration

Current automation does not:

- infer security meaning from unstructured evidence
- determine whether a requirement is met
- create an authoritative applicability conclusion from document text
- generate response narratives
- propose evidence citations
- summarize assurance reports as a security conclusion
- predict findings

OCR or other document technology that uses model-based inference requires explicit approval. The default current scope is native-text extraction and manual handling for unsupported scans.

## Current evidence flow

Evidence may be uploaded at request, assessment, or subject level. A human connects an exact immutable evidence version to a requirement through a citation.

~~~text
Request/assessment/subject evidence
  -> EVIDENCE_ITEM
       -> immutable EVIDENCE_VERSION
            -> human-created EVIDENCE_CITATION
                 -> ASSESSMENT_REQUIREMENT
~~~

One document may support many requirements through separate citations. Evidence replacement creates a new version, preserves historical citations, and triggers deterministic revalidation status.

## Current rule-based selection

Requirement selection is reproducible from structured inputs and published rule versions.

~~~text
structured intake
  + assessment type
  + subject properties
  + classification/risk values
  + rule-set version
  + requirement-set versions
  = assessment requirement snapshot
~~~

Examples:

~~~text
EXTERNAL_SAAS
  -> External SaaS Baseline

has_on_prem_component = true
  -> On-Premises Component Overlay
  -> Infrastructure Assessment

stores_restricted_data = true
  -> Restricted Data Overlay

uses_federated_authentication = true
  -> ADS Federation Requirements
~~~

Store the rule identifier, rule version, input snapshot, selection source, and time for every resolved requirement.

## Current metadata augmentation

Automation can augment metadata from authoritative structured sources or explicit mappings.

Examples:

- application catalog identifier to application owner
- technology catalog identifier to product/version owner
- structured certificate fields to issuer and expiration
- approved integration response to hosting-region metadata

Low-risk configured fields may be applied automatically. Sensitive categorization fields use a source-neutral proposal requiring human confirmation.

~~~text
METADATA_CHANGE_PROPOSAL
  source_type = RULE | INTEGRATION
  status = PROPOSED | APPROVED | APPROVED_WITH_EDIT | REJECTED | APPLIED
~~~

AI is a reserved future source type and is rejected by current validation.

## Current workflow versions

Publish deterministic definitions such as:

~~~text
External Assessment - Deterministic v1
Internal Application Assessment - Deterministic v1
Infrastructure Assessment - Deterministic v1
~~~

Active assessments remain on their selected immutable workflow version.

## Current data model

Implement now:

- evidence items, immutable versions, and scope links
- human-created evidence citations
- versioned requirement catalog and requirement sets
- assessment requirement snapshots
- requirement work packages and assignments
- response drafts and immutable submissions
- responder assertions
- reviewer determinations
- final decisions
- append-only comments and justifications
- assessment-owned findings, finding-to-requirement and finding-to-subject links
- remediation cases, corrective action plans, action items, and external issue references
- deterministic validation work after external issue resolution
- inbound integration event deduplication and reconciliation
- metadata proposals from RULE or INTEGRATION
- audit, outbox, processed-event, and RDBMS status projections

## Future AI-assisted flow

~~~text
Deterministic evidence processing
  -> AI analysis
       -> metadata proposals
       -> applicability proposals
       -> response-prefill proposals
       -> citation proposals
       -> missing-evidence proposals
  -> mandatory human review
       -> approve
       -> approve with edits
       -> reject
  -> deterministic apply command
       -> authoritative draft/citation/scope change
~~~

AI output is a proposal, never an authoritative assertion, determination, final decision, finding, exception, or closure authorization.

## Future data model

Potential future entities:

~~~text
AI_ANALYSIS_RUN
AI_ANALYSIS_INPUT
AI_ANALYSIS_OUTPUT
AI_POLICY_RESULT
AI_HUMAN_REVIEW

REQUIREMENT_APPLICABILITY_PROPOSAL
REQUIREMENT_PREFILL_PROPOSAL
PREFILL_PROPOSAL_CITATION
~~~

Future provenance includes:

- provider, model, and model version
- prompt/template identifier and version
- exact evidence-version inputs
- input snapshot hash
- raw and parsed output snapshots
- confidence and policy results
- requesting actor and supervising actor
- human approval, edits, rejection, and justification
- resulting authoritative command identifiers

## Future workflow versions

Publish separate definitions:

~~~text
External Assessment - AI-Assisted v2
Internal Application Assessment - AI-Assisted v2
Infrastructure Assessment - AI-Assisted v2
~~~

Do not modify active deterministic definitions to insert AI steps. Adoption can be limited by assessment type, organization, pilot group, or policy.

## Future safeguards

Before enabling AI:

1. Approve use cases and data classifications.
2. Define permitted models and hosting boundaries.
3. Implement prompt and model version governance.
4. Establish evaluation and quality thresholds.
5. Enforce human approval and completion authority.
6. Test prompt injection and malicious-document handling.
7. Implement cost, latency, and failure controls.
8. Provide disable, rollback, replay, and retirement procedures.
9. Confirm that deterministic ISRP operation remains available.
