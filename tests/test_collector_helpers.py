from datetime import datetime, timezone

from youtube.collector import age_hours


def test_age_hours():
    now = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)
    assert age_hours("2026-09-14T06:30:00Z", now) == 2.5


def test_age_hours_never_negative():
    now = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)
    assert age_hours("2026-09-14T10:00:00Z", now) == 0.0
