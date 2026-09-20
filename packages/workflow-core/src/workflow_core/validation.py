from __future__ import annotations

from typing import Any

from .errors import ValidationError


STEP_TYPES = {"HUMAN_TASK", "DECISION", "AUTOMATED_TASK", "FORK", "JOIN", "MILESTONE", "END"}


def validate_template(template: dict[str, Any]) -> None:
    steps = template.get("steps", [])
    transitions = template.get("transitions", [])
    if not steps:
        raise ValidationError("A workflow needs at least one step")
    keys = [step.get("key") for step in steps]
    if None in keys or len(keys) != len(set(keys)):
        raise ValidationError("Step keys must be present and unique")
    known = set(keys)
    incoming = {key: 0 for key in keys}
    outgoing = {key: 0 for key in keys}
    adjacency: dict[str, list[str]] = {key: [] for key in keys}
    for step in steps:
        if step.get("type") not in STEP_TYPES:
            raise ValidationError(f"Unsupported step type: {step.get('type')}")
        if step.get("type") == "JOIN" and step.get("join_rule") not in {"ALL", "ANY"}:
            raise ValidationError(f"JOIN step '{step['key']}' requires join_rule ALL or ANY")
    for edge in transitions:
        source, destination = edge.get("from_step"), edge.get("to_step")
        if source not in known or destination not in known:
            raise ValidationError(f"Unknown transition endpoint: {source} -> {destination}")
        incoming[destination] += 1
        outgoing[source] += 1
        adjacency[source].append(destination)
    roots = [key for key, count in incoming.items() if count == 0]
    if len(roots) != 1:
        raise ValidationError("Workflow needs exactly one root step; use a FORK for parallel starts")
    reachable: set[str] = set()
    pending = list(roots)
    while pending:
        key = pending.pop()
        if key in reachable:
            continue
        reachable.add(key)
        pending.extend(adjacency[key])
    unreachable = known - reachable
    if unreachable:
        raise ValidationError(f"Unreachable steps: {', '.join(sorted(unreachable))}")
    if not any(step["type"] == "END" for step in steps):
        raise ValidationError("Workflow needs an END step")
