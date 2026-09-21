"""Execution categories a node instance can occupy.

A node has two state dimensions: the FSM state, which the definition controls,
and the execution category, which the engine controls. A state declares which
category it maps to, so the engine never has to guess from the state's name.
"""

from __future__ import annotations


STATE_CATEGORIES = {
    "NOT_READY", "READY", "ACTIVE", "WAITING",
    "COMPLETED", "SKIPPED", "FAILED", "CANCELLED",
}

# What a node does to its workflow when it fails, and what a parent does when a
# required child fails.
FAILURE_POLICIES = {"FAIL_WORKFLOW", "SUSPEND", "CONTINUE"}
CHILD_FAILURE_POLICIES = {"FAIL", "CONTINUE"}
# What happens when a node passes its due time while still open.
BREACH_ACTIONS = {"NOTIFY", "ESCALATE", "FAIL"}

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
