"""Time utilities for UTC conversion, ISO formatting, and video age calculations."""

from __future__ import annotations

from datetime import UTC, datetime


def utc(value: datetime | str) -> datetime:
    """Normalize a datetime or ISO timestamp string to UTC.

    Requires an explicit timezone when passing a datetime object.

    Args:
        value: An ISO-8601 string or timezone-aware datetime.

    Returns:
        A datetime object in UTC timezone.
    """
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if dt.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return dt.astimezone(UTC)


def now_utc() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    """Format a datetime as a UTC ISO-8601 string ending in 'Z'.

    Args:
        value: A datetime object.

    Returns:
        An ISO-8601 string such as '2026-09-21T14:30:00Z'.
    """
    return utc(value).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    """Parse an ISO-8601 string into a UTC datetime.

    Args:
        value: An ISO-8601 formatted timestamp string.

    Returns:
        A timezone-aware datetime in UTC.
    """
    return utc(value)


def age_hours(published_at: str, current_time: datetime) -> float:
    """Calculate the age of a video in hours relative to a reference time.

    Formula:
        age_hours = max((current_time - published_at).total_seconds() / 3600.0, 0.0)

    Args:
        published_at: ISO-8601 publication timestamp.
        current_time: Timezone-aware reference timestamp.

    Returns:
        Non-negative elapsed time in hours.
    """
    delta = utc(current_time) - utc(published_at)
    return max(delta.total_seconds() / 3600.0, 0.0)
