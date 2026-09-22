"""The guard language.

Deliberately small and non-executable: a rule is data, so a workflow definition
can never run arbitrary code. Every operator is total — it answers true or
false for any pair of values rather than raising on a type mismatch — because a
guard evaluated mid-transaction must not be able to crash a workflow.
"""

from __future__ import annotations

from typing import Any

from .errors import ValidationError


MISSING = object()

COMPARISONS = {"gt", "gte", "lt", "lte"}
OPERATORS = {
    "eq", "ne", "in", "not_in", "exists", "truthy",
    "gt", "gte", "lt", "lte", "contains", "starts_with",
}
COMPOSERS = {"all", "any", "not"}


def resolve(field: Any, context: dict[str, Any]) -> Any:
    """Read a dotted path: 'subject.classification' finds a nested value.

    Returns MISSING when any segment is absent, which is what lets `exists`
    distinguish an absent fact from one set to null.
    """
    if field is None:
        return MISSING
    current: Any = context
    for part in str(field).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return MISSING
    return current


def _ordered(actual: Any, expected: Any, operator: str) -> bool:
    """Ordered comparison that is false, never an error, on mismatched types."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return False
    if not isinstance(actual, (int, float)) or not isinstance(expected, (int, float)):
        return False
    if operator == "gt":
        return actual > expected
    if operator == "gte":
        return actual >= expected
    if operator == "lt":
        return actual < expected
    return actual <= expected


def evaluate(rule: dict[str, Any] | None, context: dict[str, Any]) -> bool:
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
    if operator not in OPERATORS:
        raise ValidationError(f"Unknown rule operator: {operator!r}")

    found = resolve(field, context)
    present = found is not MISSING
    actual = None if not present else found
    expected = rule.get("value")

    if operator == "exists":
        return present == (True if expected is None else bool(expected))
    if operator == "truthy":
        return bool(actual) == (True if expected is None else bool(expected))
    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    if operator == "in":
        return actual in expected if isinstance(expected, (list, tuple, set, str)) else False
    if operator == "not_in":
        return actual not in expected if isinstance(expected, (list, tuple, set, str)) else True
    if operator == "contains":
        return expected in actual if isinstance(actual, (list, tuple, set, str)) else False
    if operator == "starts_with":
        return isinstance(actual, str) and isinstance(expected, str) and actual.startswith(expected)
    return _ordered(actual, expected, operator)


def validate_rule(rule: Any, label: str) -> None:
    """Reject a malformed or unknown rule at publication time.

    A guard that names an operator orchestration does not implement used to evaluate
    false forever, silently routing every workflow down the wrong branch.
    """
    if rule is None:
        return
    if not isinstance(rule, dict):
        raise ValidationError(f"{label} must be an object")
    if not rule:
        return
    for composer in ("all", "any"):
        if composer in rule:
            items = rule[composer]
            if not isinstance(items, list) or not items:
                raise ValidationError(f"{label} '{composer}' needs a non-empty list")
            for item in items:
                validate_rule(item, label)
            return
    if "not" in rule:
        validate_rule(rule["not"], label)
        return
    operator = rule.get("operator", rule.get("op", "eq"))
    if operator not in OPERATORS:
        raise ValidationError(
            f"{label} uses unknown operator '{operator}'. "
            f"Supported: {', '.join(sorted(OPERATORS))}"
        )
    if not rule.get("field"):
        raise ValidationError(f"{label} needs a 'field'")
    if (operator in COMPARISONS
            and (isinstance(rule.get("value"), bool)
                 or not isinstance(rule.get("value"), (int, float)))):
        raise ValidationError(f"{label} operator '{operator}' needs a numeric 'value'")
