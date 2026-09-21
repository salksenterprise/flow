from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


StepType = Literal[
    "HUMAN_TASK", "DECISION", "AUTOMATED_TASK", "FORK", "JOIN",
    "SUBWORKFLOW", "WAIT_SIGNAL", "TIMER", "MILESTONE", "END",
]


class ActorContext(BaseModel):
    """What a client may say about itself: who it claims to be, nothing more.

    Roles, groups and permissions are deliberately absent. Authority comes from
    the trusted actor header the gateway injects, never from the request body,
    and whatever arrives here is replaced before the engine sees it.
    """

    actor_id: str
    actor_type: str = "USER"
    organization_id: str | None = None


Actor = str | ActorContext


class StepDefinitionIn(BaseModel):
    key: str
    name: str
    type: StepType
    stage: str = ""
    description: str = ""
    assignment_role: str | None = None
    join_rule: Literal["ALL", "ANY", "N_OF_M", "ALL_REQUIRED"] | None = None
    fsm: dict[str, Any] | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)


class TransitionIn(BaseModel):
    from_step: str
    to_step: str
    condition: dict[str, Any] | None = None
    priority: int = 100


class TemplateImport(BaseModel):
    key: str
    name: str
    description: str = ""
    domain: str = "GENERIC"
    version: int = 1
    publish: bool = True
    lifecycle_fsm: dict[str, Any] | None = None
    steps: list[StepDefinitionIn]
    transitions: list[TransitionIn]


class SubjectIn(BaseModel):
    subject_type: str
    subject_id: str
    source_system: str
    relationship: str = "PRIMARY"


class WorkflowStart(BaseModel):
    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workflow_version_id: int
    title: str
    business_type: str | None = None
    business_key: str | None = None
    correlation_id: str | None = None
    actor: Actor = "api.user"
    variables: dict[str, Any] = Field(default_factory=dict)
    subjects: list[SubjectIn] = Field(default_factory=list)
    parent_step_instance_id: int | None = None
    relationship_type: str | None = None
    relationship_key: str | None = None
    required: bool = True


class StepAction(BaseModel):
    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action: str
    actor: Actor = "api.user"
    expected_revision: int | None = None
    reason: str | None = None
    assignee: str | None = None
    assignee_type: str = "USER"
    organization_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RepairAction(BaseModel):
    """A manual intervention. Permission and reason are both mandatory."""

    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action: Literal["skip_step", "force_complete_step", "retry_step", "reassign_step"]
    actor: Actor = "api.user"
    expected_revision: int | None = None
    reason: str
    assignee: str | None = None
    assignee_type: str = "USER"
    organization_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class VersionMigration(BaseModel):
    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_version_id: int
    actor: Actor = "api.user"
    expected_revision: int | None = None
    reason: str


class WorkflowAction(BaseModel):
    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action: str
    actor: Actor = "api.user"
    expected_revision: int | None = None
    reason: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class FactUpdate(BaseModel):
    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    actor: Actor = "api.user"
    expected_revision: int | None = None
    facts: dict[str, Any]
    source_type: str = "COMMAND"
    source_reference: str | None = None


class SignalIn(BaseModel):
    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    actor: Actor = "api.user"
    signal_type: str
    correlation_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    apply_facts: bool = False


class ExternalEventIn(BaseModel):
    connector_name: str
    provider_event_id: str
    event_type: str
    correlation_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    apply_facts: bool = False


class AutomationResult(BaseModel):
    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    actor: Actor = "automation.worker"
    success: bool = True
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    retry_after_seconds: int | None = None


class WebhookSubscriptionIn(BaseModel):
    name: str
    target_url: str
    event_types: list[str] = Field(default_factory=list)
    secret: str | None = None
