from __future__ import annotations

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

    @model_validator(mode="after")
    def validate_graph(self):
        keys = [step.key for step in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError("Step keys must be unique")
        known = set(keys)
        for edge in self.transitions:
            if edge.from_step not in known or edge.to_step not in known:
                raise ValueError(f"Unknown transition endpoint: {edge.from_step} -> {edge.to_step}")
        return self


class SubjectIn(BaseModel):
    subject_type: str
    subject_id: str
    source_system: str
    relationship: str = "PRIMARY"


class WorkflowStart(BaseModel):
    workflow_version_id: int
    title: str
    created_by: str = "demo.user"
    input: dict[str, Any] = Field(default_factory=dict)
    subjects: list[SubjectIn] = Field(default_factory=list)


class StepAction(BaseModel):
    action: Literal[
        "assign", "start", "wait", "resume", "request_clarification",
        "respond", "complete", "skip", "fail", "cancel"
    ]
    actor: str = "demo.user"
    assignee: str | None = None
    assignee_type: str = "USER"
    payload: dict[str, Any] = Field(default_factory=dict)

