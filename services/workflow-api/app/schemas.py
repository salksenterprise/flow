from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


StepType = Literal["HUMAN_TASK", "DECISION", "AUTOMATED_TASK", "FORK", "JOIN", "MILESTONE", "END"]


class StepDefinitionIn(BaseModel):
    key: str
    name: str
    type: StepType
    stage: str = ""
    description: str = ""
    assignment_role: str | None = None
    join_rule: Literal["ALL", "ANY"] | None = None
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
    actor: str = "api.user"
    variables: dict[str, Any] = Field(default_factory=dict)
    subjects: list[SubjectIn] = Field(default_factory=list)


class StepAction(BaseModel):
    command_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action: Literal[
        "assign", "start", "wait", "resume", "request_clarification",
        "respond", "complete", "skip", "fail", "cancel",
    ]
    actor: str = "api.user"
    expected_revision: int | None = None
    assignee: str | None = None
    assignee_type: str = "USER"
    payload: dict[str, Any] = Field(default_factory=dict)


class WebhookSubscriptionIn(BaseModel):
    name: str
    target_url: str
    event_types: list[str] = Field(default_factory=list)
    secret: str | None = None

