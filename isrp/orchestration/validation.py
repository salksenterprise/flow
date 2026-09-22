from __future__ import annotations

from typing import Any

from .errors import ValidationError
from .rules import validate_rule
from .calendars import validate_calendar
from .states import (
    ASSESSMENT_FAILURE_POLICIES, BREACH_ACTIONS, EXECUTION_MODES, FAILURE_POLICIES,
    FUTURE_EXECUTION_MODES, PERFORMED_STEP_TYPES, STATE_CATEGORIES,
)


STEP_TYPES = {
    "HUMAN_TASK", "DECISION", "AUTOMATED_TASK", "FORK", "JOIN",
    "ASSESSMENT", "WAIT_SIGNAL", "TIMER", "MILESTONE", "END",
}
JOIN_RULES = {"ALL", "ANY", "N_OF_M", "ALL_REQUIRED"}


def validate_fsm(spec: dict[str, Any], label: str) -> None:
    states = [item if isinstance(item, str) else item.get("key") for item in spec.get("states", [])]
    if not states or None in states or len(states) != len(set(states)):
        raise ValidationError(f"{label} states must be present and unique")
    for item in spec.get("states", []):
        category = item.get("category") if isinstance(item, dict) else None
        if category is not None and category not in STATE_CATEGORIES:
            raise ValidationError(
                f"{label} state '{item.get('key')}' declares unknown category "
                f"'{category}'. Supported: {', '.join(sorted(STATE_CATEGORIES))}"
            )
    if spec.get("initial_state") not in states:
        raise ValidationError(f"{label} initial_state must name a defined state")
    seen: set[tuple[str, str]] = set()
    for transition in spec.get("transitions", []):
        source, target, action = transition.get("from"), transition.get("to"), transition.get("action")
        if source not in states or target not in states or not action:
            raise ValidationError(f"{label} has an invalid transition")
        validate_rule(transition.get("guard"), f"{label} guard on {source}/{action}")
        key = (source, action)
        if key in seen:
            raise ValidationError(f"{label} has duplicate transition {source}/{action}")
        seen.add(key)


def validate_template(template: dict[str, Any]) -> None:
    steps = template.get("steps", [])
    transitions = template.get("transitions", [])
    if not steps:
        raise ValidationError("A workflow needs at least one step")
    if template.get("lifecycle_fsm"):
        validate_fsm(template["lifecycle_fsm"], "Lifecycle FSM")
    keys = [step.get("key") for step in steps]
    if None in keys or len(keys) != len(set(keys)):
        raise ValidationError("Step keys must be present and unique")
    known = set(keys)
    incoming = {key: 0 for key in keys}
    outgoing = {key: 0 for key in keys}
    adjacency: dict[str, list[str]] = {key: [] for key in keys}
    reverse: dict[str, list[str]] = {key: [] for key in keys}
    by_key = {step["key"]: step for step in steps}
    for step in steps:
        if step.get("type") not in STEP_TYPES:
            raise ValidationError(f"Unsupported step type: {step.get('type')}")
        if step.get("type") == "JOIN" and step.get("join_rule") not in JOIN_RULES:
            raise ValidationError(f"JOIN step '{step['key']}' requires a supported join_rule")
        if step.get("type") == "WAIT_SIGNAL" and not step.get("configuration", {}).get("signal_type"):
            raise ValidationError(f"WAIT_SIGNAL step '{step['key']}' requires configuration.signal_type")
        if step.get("type") == "TIMER" and "delay_seconds" not in step.get("configuration", {}):
            raise ValidationError(f"TIMER step '{step['key']}' requires configuration.delay_seconds")
        if step.get("fsm"):
            validate_fsm(step["fsm"], f"Step FSM '{step['key']}'")
        mode = step.get("execution_mode")
        if mode is not None:
            if mode in FUTURE_EXECUTION_MODES:
                raise ValidationError(
                    f"Step '{step['key']}' declares execution mode '{mode}'. AI modes "
                    "are reserved for separately published future definitions and are "
                    "rejected by the current release.")
            if mode not in EXECUTION_MODES:
                raise ValidationError(
                    f"Step '{step['key']}' declares unknown execution mode '{mode}'. "
                    f"Supported: {', '.join(sorted(EXECUTION_MODES))}")
            if step.get("type") not in PERFORMED_STEP_TYPES:
                raise ValidationError(
                    f"Step '{step['key']}' is a {step.get('type')} node, which the "
                    "engine drives; it cannot declare an execution mode")
        configuration = step.get("configuration", {})
        on_failure = configuration.get("on_failure")
        if on_failure is not None and on_failure not in FAILURE_POLICIES:
            raise ValidationError(
                f"Step '{step['key']}' declares unknown on_failure '{on_failure}'. "
                f"Supported: {', '.join(sorted(FAILURE_POLICIES))}")
        if step.get("type") == "ASSESSMENT" and not configuration.get(
                "assessment_workflow_version_id"):
            raise ValidationError(
                f"ASSESSMENT step '{step['key']}' requires "
                "configuration.assessment_workflow_version_id")
        assessment_policy = configuration.get("assessment_failure_policy")
        if assessment_policy is not None and assessment_policy not in ASSESSMENT_FAILURE_POLICIES:
            raise ValidationError(
                f"Step '{step['key']}' declares unknown assessment_failure_policy "
                f"'{assessment_policy}'. Supported: "
                f"{', '.join(sorted(ASSESSMENT_FAILURE_POLICIES))}")
        validate_calendar(configuration.get("calendar"), f"Step '{step['key']}' calendar")
        due = configuration.get("due_in_seconds")
        if due is not None and (not isinstance(due, int) or isinstance(due, bool) or due <= 0):
            raise ValidationError(
                f"Step '{step['key']}' due_in_seconds must be a positive whole number")
        breach = configuration.get("on_breach")
        if breach is not None and breach not in BREACH_ACTIONS:
            raise ValidationError(
                f"Step '{step['key']}' declares unknown on_breach '{breach}'. "
                f"Supported: {', '.join(sorted(BREACH_ACTIONS))}")
        if breach == "ESCALATE" and not configuration.get("escalate_to"):
            raise ValidationError(
                f"Step '{step['key']}' sets on_breach ESCALATE without an escalate_to")
        for candidate in step.get("configuration", {}).get("candidates", []):
            if not candidate.get("type") or not candidate.get("value"):
                raise ValidationError(
                    f"Step '{step['key']}' has a candidate without a type and value")
    edge_keys: set[tuple[str, str]] = set()
    for edge in transitions:
        source, destination = edge.get("from_step"), edge.get("to_step")
        if source not in known or destination not in known:
            raise ValidationError(f"Unknown transition endpoint: {source} -> {destination}")
        if (source, destination) in edge_keys:
            raise ValidationError(f"Duplicate transition: {source} -> {destination}")
        validate_rule(edge.get("condition"), f"Condition on {source} -> {destination}")
        if by_key[source]["type"] == "END":
            raise ValidationError(f"END step '{source}' cannot have outgoing transitions")
        edge_keys.add((source, destination))
        incoming[destination] += 1
        outgoing[source] += 1
        adjacency[source].append(destination)
        reverse[destination].append(source)
    roots = [key for key, count in incoming.items() if count == 0]
    if len(roots) != 1:
        raise ValidationError("Workflow needs exactly one root step; use a FORK for parallel starts")
    ends = [step["key"] for step in steps if step["type"] == "END"]
    if not ends:
        raise ValidationError("Workflow needs an END step")
    for step in steps:
        if step["type"] != "END" and outgoing[step["key"]] == 0:
            raise ValidationError(f"Non-END step '{step['key']}' has no outgoing transition")
    color: dict[str, int] = {key: 0 for key in keys}

    def visit(key: str) -> None:
        if color[key] == 1:
            raise ValidationError("Workflow graph must be acyclic")
        if color[key] == 2:
            return
        color[key] = 1
        for child in adjacency[key]:
            visit(child)
        color[key] = 2

    visit(roots[0])
    unreachable = {key for key, value in color.items() if value == 0}
    if unreachable:
        raise ValidationError(f"Unreachable steps: {', '.join(sorted(unreachable))}")
    can_reach_end: set[str] = set()
    pending = list(ends)
    while pending:
        key = pending.pop()
        if key in can_reach_end:
            continue
        can_reach_end.add(key)
        pending.extend(reverse[key])
    stranded = known - can_reach_end
    if stranded:
        raise ValidationError(f"Steps cannot reach an END: {', '.join(sorted(stranded))}")
