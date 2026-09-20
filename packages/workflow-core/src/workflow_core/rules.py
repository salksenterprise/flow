from __future__ import annotations

from typing import Any


def evaluate(rule: dict[str, Any] | None, context: dict[str, Any]) -> bool:
    """Evaluate a deliberately small, non-executable rule language."""
    if not rule:
        return True
    if "all" in rule:
        return all(evaluate(item, context) for item in rule["all"])
    if "any" in rule:
        return any(evaluate(item, context) for item in rule["any"])
    if "not" in rule:
        return not evaluate(rule["not"], context)

    field = rule.get("field")
    operator = rule.get("operator", rule.get("op", "eq"))
    actual = context.get(field)
    expected = rule.get("value")
    operations = {
        "eq": lambda: actual == expected,
        "ne": lambda: actual != expected,
        "in": lambda: actual in (expected or []),
        "not_in": lambda: actual not in (expected or []),
        "exists": lambda: (field in context) == bool(expected),
        "truthy": lambda: bool(actual) == (True if expected is None else bool(expected)),
    }
    return operations.get(operator, lambda: False)()

