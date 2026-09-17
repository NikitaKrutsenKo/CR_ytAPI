from __future__ import annotations

from datetime import datetime


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def age_hours(published_at: str, current_time: datetime) -> float:
    delta = current_time - parse_utc(published_at)
    return max(delta.total_seconds() / 3600.0, 0.0)
