"""The orchestration vocabulary: what is orchestrated, and the states it moves through.

A node has two state dimensions: the FSM state, which the definition controls,
and the execution category, which the engine controls. A state declares which
category it maps to, so the engine never has to guess from the state's name.
"""

from __future__ import annotations

from typing import NamedTuple


class Owner(NamedTuple):
    """What a run of a workflow belongs to.

    There is no workflow instance. ISRP orchestrates exactly two things, and
    every runtime row -- node, fact, signal, job, timer, event, receipt --
    names one of them. A plain tuple, so a caller can pass ("ISRP_REQUEST", 3)
    without importing anything.
    """

    type: str
    id: int


REQUEST = "ISRP_REQUEST"
ASSESSMENT = "ISRP_ASSESSMENT"
OWNER_TYPES = (REQUEST, ASSESSMENT)


STATE_CATEGORIES = {
    "NOT_READY", "READY", "ACTIVE", "WAITING",
    "COMPLETED", "SKIPPED", "FAILED", "CANCELLED",
}

# What a node does to its workflow when it fails, and what a request does when
# a required assessment fails.
FAILURE_POLICIES = {"FAIL_WORKFLOW", "SUSPEND", "CONTINUE"}
ASSESSMENT_FAILURE_POLICIES = {"FAIL", "CONTINUE"}
# What happens when a node passes its due time while still open.
BREACH_ACTIONS = {"NOTIFY", "ESCALATE", "FAIL"}

# Who or what performs a step. Only nodes somebody actually performs carry one;
# FORK, JOIN, MILESTONE, END, WAIT_SIGNAL, TIMER and ASSESSMENT are driven by
# the engine and have none.
#
# The AI modes are declared so that publication can name them when it refuses
# them. They are reserved for separately published future definitions, and the
# current release rejects any definition that uses one. A generic engine had no
# field for this at all, which is why the requirement could not be enforced
# before orchestration became ISRP's own.
EXECUTION_MODES = {"HUMAN", "AUTOMATION"}
FUTURE_EXECUTION_MODES = {"AI_ASSISTED_HUMAN", "AI_AUTOMATED_SUPERVISED"}
PERFORMED_STEP_TYPES = {"HUMAN_TASK", "DECISION", "AUTOMATED_TASK"}
DEFAULT_EXECUTION_MODE = {
    "HUMAN_TASK": "HUMAN", "DECISION": "HUMAN", "AUTOMATED_TASK": "AUTOMATION",
}


def resolve_execution_mode(step_type: str, declared: str | None) -> str | None:
    """A declared mode wins; otherwise infer from the node type."""
    if declared:
        return declared
    return DEFAULT_EXECUTION_MODE.get(step_type)

SATISFIED_EXECUTION = {"COMPLETED", "SKIPPED"}
TERMINAL_EXECUTION = {"COMPLETED", "SKIPPED", "FAILED", "CANCELLED"}

DEFAULT_STATE_CATEGORY = {
    "NOT_READY": "NOT_READY", "READY": "READY", "ASSIGNED": "READY",
    "IN_PROGRESS": "ACTIVE", "RESPONSE_RECEIVED": "ACTIVE", "RUNNING": "ACTIVE",
    "WAITING": "WAITING", "CLARIFICATION_REQUIRED": "WAITING", "QUEUED": "WAITING",
    "COMPLETED": "COMPLETED", "SUCCEEDED": "COMPLETED", "DONE": "COMPLETED",
    "SKIPPED": "SKIPPED", "FAILED": "FAILED", "CANCELLED": "CANCELLED",
}


def resolve_category(state_key: str, terminal: bool, declared: str | None) -> str:
    """The execution category of an FSM state.

    A declared category always wins. Otherwise a known state name is used, and
    anything else falls back on whether the state is terminal, which is the
    only safe guess for a definition written before categories existed.
    """
    if declared:
        return declared
    if state_key in DEFAULT_STATE_CATEGORY:
        return DEFAULT_STATE_CATEGORY[state_key]
    return "COMPLETED" if terminal else "ACTIVE"
