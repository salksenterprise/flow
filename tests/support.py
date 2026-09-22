"""Helpers shared by the orchestration tests."""

from __future__ import annotations

from typing import Any

from isrp.orchestration import Owner


def owner(aggregate: dict[str, Any]) -> Owner:
    """The (owner_type, id) pair naming the aggregate a result describes."""
    return Owner(aggregate["owner_type"], aggregate["id"])


def aggregate_args(aggregate) -> tuple[str, str]:
    """(aggregate_type, aggregate_id) for a raw event_log query.

    The id is text in the log, because a host aggregate need not have an
    integer key.
    """
    return (aggregate["owner_type"], str(aggregate["id"]))
