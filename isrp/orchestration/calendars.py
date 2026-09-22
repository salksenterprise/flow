"""Deadlines that respect working hours.

A four-hour service level does not mean four hours of wall clock when the team
works nine to five. Without a calendar, every deadline set late on a Friday
breaches over the weekend and the breach tells an operator nothing.

A calendar is data, like everything else a definition carries:

    {"business_days": [0, 1, 2, 3, 4],
     "opens_at": "09:00", "closes_at": "17:00",
     "holidays": ["2026-12-25"]}

Monday is 0. Omit the calendar entirely for plain elapsed time.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from .errors import ValidationError


DEFAULT_BUSINESS_DAYS = (0, 1, 2, 3, 4)
DEFAULT_OPENS_AT = "09:00"
DEFAULT_CLOSES_AT = "17:00"

# A deadline is resolved by walking forward one working day at a time. The
# bound stops a calendar that can never satisfy it from looping forever.
MAX_DAYS_AHEAD = 3660


def parse_clock(value: str, label: str) -> time:
    try:
        hour, minute = (int(part) for part in str(value).split(":", 1))
        return time(hour=hour, minute=minute)
    except (TypeError, ValueError) as error:
        raise ValidationError(f"{label} must look like 'HH:MM', got {value!r}") from error


def validate_calendar(calendar: Any, label: str = "calendar") -> None:
    if calendar is None:
        return
    if not isinstance(calendar, dict):
        raise ValidationError(f"{label} must be an object")
    days = calendar.get("business_days", DEFAULT_BUSINESS_DAYS)
    if not isinstance(days, (list, tuple)) or not days:
        raise ValidationError(f"{label}.business_days must be a non-empty list")
    if any(not isinstance(day, int) or not 0 <= day <= 6 for day in days):
        raise ValidationError(f"{label}.business_days entries must be 0 (Monday) to 6")
    opens = parse_clock(calendar.get("opens_at", DEFAULT_OPENS_AT), f"{label}.opens_at")
    closes = parse_clock(calendar.get("closes_at", DEFAULT_CLOSES_AT), f"{label}.closes_at")
    if opens >= closes:
        raise ValidationError(f"{label}.opens_at must be earlier than closes_at")
    for holiday in calendar.get("holidays", []):
        try:
            date.fromisoformat(str(holiday))
        except ValueError as error:
            raise ValidationError(
                f"{label}.holidays entries must be ISO dates, got {holiday!r}") from error


def deadline(start: datetime, seconds: int, calendar: dict[str, Any] | None) -> datetime:
    """When `seconds` of working time from `start` runs out."""
    if not calendar:
        return start + timedelta(seconds=seconds)
    validate_calendar(calendar)
    days = set(calendar.get("business_days", DEFAULT_BUSINESS_DAYS))
    opens = parse_clock(calendar.get("opens_at", DEFAULT_OPENS_AT), "opens_at")
    closes = parse_clock(calendar.get("closes_at", DEFAULT_CLOSES_AT), "closes_at")
    holidays = {str(item) for item in calendar.get("holidays", [])}

    remaining = max(int(seconds), 0)
    cursor = start
    for _ in range(MAX_DAYS_AHEAD):
        if remaining <= 0:
            return cursor
        working = cursor.weekday() in days and cursor.date().isoformat() not in holidays
        day_open = datetime.combine(cursor.date(), opens, tzinfo=cursor.tzinfo)
        day_close = datetime.combine(cursor.date(), closes, tzinfo=cursor.tzinfo)
        if not working or cursor >= day_close:
            cursor = datetime.combine(
                (cursor + timedelta(days=1)).date(), opens, tzinfo=cursor.tzinfo)
            continue
        if cursor < day_open:
            cursor = day_open
        available = int((day_close - cursor).total_seconds())
        if available >= remaining:
            return cursor + timedelta(seconds=remaining)
        remaining -= available
        cursor = datetime.combine(
            (cursor + timedelta(days=1)).date(), opens, tzinfo=cursor.tzinfo)
    raise ValidationError(
        f"No deadline within {MAX_DAYS_AHEAD} days satisfies this calendar")
