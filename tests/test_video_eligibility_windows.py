from datetime import UTC, datetime, timedelta

import pytest

from research.discovery import YouTubeDiscoveryCollector
from research.domain import (
    CandidateTopic,
    CollectionRequest,
    ExperimentConfig,
    Mode,
    WindowResolver,
    iso,
)


NOW = datetime(2026, 9, 19, 10, tzinfo=UTC)


def request(mode="ROLLING", start="2026-09-18T10:00:00Z", end="2026-09-19T10:00:00Z"):
    return CollectionRequest(
        CandidateTopic.named("AI"),
        window_mode=mode,
        requested_from=start,
        requested_to=end,
    )


def test_rolling_cap_preserves_exact_width_and_moves_with_elapsed_time():
    initial = WindowResolver.resolve(request(), NOW, 1)
    assert iso(initial.effective_from) == "2026-09-18T09:00:00Z"
    assert iso(initial.effective_to) == "2026-09-19T09:00:00Z"
    assert initial.window_width_seconds == 24 * 3600
    for hours in (0, 1, 5):
        current = WindowResolver.resolve(
            request(), NOW + timedelta(hours=hours), 1, elapsed_seconds=hours * 3600, initial=initial
        )
        assert current.effective_from == initial.effective_from + timedelta(hours=hours)
        assert current.effective_to == initial.effective_to + timedelta(hours=hours)
        assert (current.effective_to - current.effective_from).total_seconds() == 24 * 3600


def test_static_caps_only_to_and_never_moves():
    selected = request("STATIC", "2026-09-15T00:00:00Z", "2026-09-25T00:00:00Z")
    initial = WindowResolver.resolve(selected, NOW, 1)
    assert iso(initial.effective_from) == "2026-09-15T00:00:00Z"
    assert iso(initial.effective_to) == "2026-09-19T09:00:00Z"
    for hours in (1, 5, 24):
        current = WindowResolver.resolve(
            selected, NOW + timedelta(hours=hours), 1, elapsed_seconds=hours * 3600, initial=initial
        )
        assert current.effective_from == initial.effective_from
        assert current.effective_to == initial.effective_to


class EligibilityClient:
    profile = "DEFAULT"

    def __init__(self, rows):
        self.rows, self.telemetry, self.search_params = rows, [], None

    def get(self, resource, params):
        if resource == "search":
            self.search_params = params
            return {
                "items": [
                    {
                        "id": {"videoId": row["id"]},
                        "snippet": {
                            "channelId": "channel",
                            "publishedAt": row["snippet"]["publishedAt"],
                            "title": row["id"],
                        },
                    }
                    for row in self.rows
                ]
            }
        return {"items": self.rows}


def item(identifier, published, views="1000"):
    statistics = {"likeCount": "1", "commentCount": "1"}
    if views is not None:
        statistics["viewCount"] = views
    return {
        "id": identifier,
        "snippet": {"channelId": "channel", "publishedAt": published, "title": identifier},
        "statistics": statistics,
    }


def test_age_and_view_boundaries_filter_only_eligible_observations():
    rows = [
        item("old", "2026-09-19T08:59:00Z", "1001"),
        item("boundary", "2026-09-19T09:00:00Z", "1000"),
        item("young", "2026-09-19T09:01:00Z", "5000"),
        item("low", "2026-09-19T08:00:00Z", "999"),
        item("missing", "2026-09-19T08:00:00Z", None),
    ]
    client = EligibilityClient(rows)
    selected = request("STATIC", "2026-09-18T09:00:00Z", "2026-09-19T09:00:00Z")
    window = WindowResolver.resolve(selected, NOW, 1)
    bundle = YouTubeDiscoveryCollector(client).collect(
        selected, now=NOW, window=window, minimum_video_age_hours=1, minimum_views=1000
    )
    assert {row.identity.video_id for row in bundle.observations} == {"old", "boundary"}
    assert {row.video_id for row in bundle.discovery} == {r["id"] for r in rows}
    assert bundle.metadata.excluded_too_young_count == 1
    assert bundle.metadata.excluded_low_views_count == 1
    assert bundle.metadata.excluded_missing_views_count == 1
    assert client.search_params["publishedBefore"] == "2026-09-19T09:00:00Z"


def test_zero_view_threshold_allows_missing_view_count():
    row = item("missing", "2026-09-19T08:00:00Z", None)
    client = EligibilityClient([row])
    selected = request("STATIC", "2026-09-18T09:00:00Z", "2026-09-19T09:00:00Z")
    bundle = YouTubeDiscoveryCollector(client).collect(selected, now=NOW, minimum_views=0)
    assert [item.identity.video_id for item in bundle.observations] == ["missing"]


def test_legacy_duration_and_rolling_hours_migrate_to_canonical_fields(monkeypatch):
    raw = {
        "mode": Mode.TREND,
        "requests": [
            {
                "topic": CandidateTopic.named("AI").__dict__,
                "window_mode": "ROLLING",
                "rolling_hours": 24,
            }
        ],
        "duration_minutes": 90,
    }
    config = ExperimentConfig.from_dict(raw)
    assert config.duration_hours == 1.5
    assert config.requests[0].requested_from and config.requests[0].requested_to
    assert (
        datetime.fromisoformat(config.requests[0].requested_to.replace("Z", "+00:00"))
        - datetime.fromisoformat(config.requests[0].requested_from.replace("Z", "+00:00"))
    ).total_seconds() == 24 * 3600
    assert "rolling_hours" not in config.requests[0].__dict__


@pytest.mark.parametrize("views,eligible", [(999, False), (1000, True), (1001, True)])
def test_view_threshold_inclusive(views, eligible):
    row = item(str(views), "2026-09-19T08:00:00Z", str(views))
    client = EligibilityClient([row])
    selected = request("STATIC", "2026-09-18T09:00:00Z", "2026-09-19T09:00:00Z")
    bundle = YouTubeDiscoveryCollector(client).collect(selected, now=NOW, minimum_views=1000)
    assert bool(bundle.observations) is eligible
