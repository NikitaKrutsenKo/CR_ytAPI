from dataclasses import replace
from datetime import UTC, datetime

import pytest

from research.discovery import YouTubeDiscoveryCollector
from research.domain import CandidateTopic, CollectionRequest, ExperimentConfig, Mode, Status
from research.profiles import ApiProfiles
from research.quota import QuotaManager, QuotaStopped
from youtube.client import YouTubeClient
from youtube.videos import YouTubeVideoService

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)


def video(identifier="v", channel="c", views=100):
    return {
        "id": identifier,
        "snippet": {"channelId": channel, "publishedAt": "2026-09-17T11:00:00Z", "title": identifier},
        "statistics": {"viewCount": str(views), "likeCount": "10", "commentCount": "1"},
    }


class FakeClient:
    profile = "DEFAULT"

    def __init__(self):
        self.telemetry, self.calls = [], []

    def get(self, resource, params):
        self.calls.append((resource, params))
        if resource == "search":
            item = video()
            item["id"] = {"videoId": "v"}
            return {"items": [item], **({"nextPageToken": "next"} if "pageToken" not in params else {})}
        if resource == "videos":
            return {"items": [video(v) for v in params["id"].split(",")]}
        if resource == "channels":
            return {
                "items": [
                    {"id": c, "contentDetails": {"relatedPlaylists": {"uploads": "p" + c}}}
                    for c in params["id"].split(",")
                ]
            }
        return {"items": [{"contentDetails": {"videoId": "r" + str(i)}} for i in range(5)]}


def test_static_rolling_incremental():
    request = CollectionRequest(CandidateTopic.named("AI"))
    start, end, effective = request.bounds(NOW, "2026-09-17T11:30:00Z")
    assert effective.minute == 25 and effective.hour == 11
    assert (end - start).total_seconds() == 86400
    static = replace(
        request,
        window_mode="STATIC",
        requested_from="2026-09-01T00:00:00Z",
        requested_to="2026-09-02T00:00:00Z",
    )
    assert static.bounds(NOW, "2026-09-17T00:00:00Z")[0] == static.bounds(NOW)[2]
    with pytest.raises(ValueError):
        replace(static, requested_from="2026-09-01T00:00:00").bounds(NOW)


def test_pagination_dedupe_missing_optional():
    client = FakeClient()
    bundle = YouTubeDiscoveryCollector(client).collect(
        CollectionRequest(CandidateTopic.named("AI"), max_pages=2), now=NOW
    )
    assert bundle.metadata.pages_received == 2
    assert bundle.metadata.duplicates_removed == 1
    assert bundle.metadata.unique_video_count == 1
    assert len(bundle.observations) == 1
    assert bundle.metadata.status == Status.COMPLETE
    assert all(resource != "channels" for resource, _ in client.calls)


def test_batched_ids():
    client = FakeClient()
    assert len(YouTubeVideoService(client).get_videos_batched([str(i) for i in range(105)])) == 105
    assert [len(p["id"].split(",")) for _, p in client.calls] == [50, 50, 5]


def test_profiles(tmp_path):
    profiles = ApiProfiles(
        tmp_path / "absent", {"YOUTUBE_API_KEY_NICK": "test-only", "YOUTUBE_API_KEY_DEFAULT": ""}
    )
    assert profiles.names == ["NICK"]
    assert profiles.resolve("NICK") == "test-only"
    assert "test-only" not in repr(profiles)
    with pytest.raises(ValueError):
        profiles.resolve("DEFAULT")


def test_quota_atomic_shared_and_separate(tmp_path):
    path = tmp_path / "quota.sqlite"
    quota = QuotaManager(path, "DEFAULT", 1, 2, 0, clock=lambda: NOW)
    quota.consume("search")
    other = QuotaManager(path, "DEFAULT", 1, 2, 0, clock=lambda: NOW)
    with pytest.raises(QuotaStopped):
        other.consume("search")
    other.consume("videos")
    assert quota.usage()["actual_local_call_count"] == 2
    estimate = quota.estimate(ExperimentConfig(Mode.TREND, (CollectionRequest(CandidateTopic.named("AI")),)))
    assert estimate.status == "WOULD EXCEED BUDGET"


def test_retry_telemetry_and_secret_safety(tmp_path):
    class Response:
        def __init__(self, status):
            self.status_code, self.ok = status, status == 200

        def json(self):
            return {"items": []}

    class Session:
        statuses = iter([429, 500, 200])

        def get(self, *args, **kwargs):
            return Response(next(self.statuses))

    quota = QuotaManager(tmp_path / "quota.sqlite", "DEFAULT")
    client = YouTubeClient("test-secret", quota=quota, session=Session(), sleeper=lambda _: None)
    assert client.get("videos", {}) == {"items": []}
    assert len(client.telemetry) == 3
    assert quota.usage()["actual_local_call_count"] == 3
    assert "test-secret" not in repr(client.telemetry)
