import math
from dataclasses import replace
from datetime import timedelta

import pytest

from research.application import ExperimentManager
from research.collection import CollectionService
from research.discovery import YouTubeDiscoveryCollector, observation
from research.domain import (
    CandidateTopic,
    CollectionRequest,
    ExperimentConfig,
    Mode,
    Status,
    iso,
)
from research.profiles import ApiProfiles
from research.quota import QuotaManager, QuotaStopped
from research.replay import ReplayService
from research.storage import read_json, read_jsonl
from research.trend import TrendEngine
from tests.test_research_flows import RunClient, make_trend_bundle
from tests.test_research_ingestion import NOW, FakeClient, video
from youtube.client import RequestCancelled, YouTubeApiError, YouTubeClient, YouTubeQuotaExceeded


@pytest.mark.parametrize(
    "error,status",
    [
        (YouTubeApiError("temporary"), Status.PARTIAL),
        (QuotaStopped("limit"), Status.QUOTA_STOPPED),
        (RequestCancelled("stop"), Status.CANCELLED),
    ],
)
def test_partial_second_page_preserves_first_page(error, status):
    class Client(FakeClient):
        def get(self, resource, params):
            if resource == "search" and "pageToken" in params:
                raise error
            return super().get(resource, params)

    result = YouTubeDiscoveryCollector(Client()).collect(
        CollectionRequest(CandidateTopic.named("AI"), max_pages=2)
    )
    assert result.metadata.status == status
    assert len(result.discovery) == 1
    assert result.metadata.pages_received == 1


def test_empty_deleted_and_missing_statistics():
    class Empty(RunClient):
        def get(self, resource, params):
            return {"items": []}

    request = CollectionRequest(CandidateTopic.named("Empty"))
    result = YouTubeDiscoveryCollector(Empty()).collect(request)
    assert result.metadata.status == Status.COMPLETE and not result.observations

    class Deleted(RunClient):
        def get(self, resource, params):
            return {"items": []} if resource == "videos" else super().get(resource, params)

    result = YouTubeDiscoveryCollector(Deleted()).collect(request)
    assert result.metadata.status == Status.PARTIAL and len(result.discovery) == 1
    item = video()
    item["statistics"] = {"viewCount": "0"}
    obs = observation(item, iso(NOW))
    assert obs.views == 0 and obs.likes is None and obs.comments is None


def test_capped_discovery_does_not_advance_watermark():
    service = CollectionService(FakeClient())
    request = CollectionRequest(CandidateTopic.named("AI"), max_pages=1)
    bundle = service.discover(request)
    assert bundle.metadata.truncated
    assert not service.watermarks
    service = CollectionService(RunClient())
    bundle = service.discover(request)
    assert service.watermarks[request.topic.topic_id] == bundle.metadata.effective_to


def test_tracking_never_searches():
    client = RunClient()
    result = CollectionService(client).track(CollectionRequest(CandidateTopic.named("AI")), ["v"])
    assert len(result.observations) == 1
    assert [r for r, _ in client.calls] == ["videos"]


def test_quota_daily_reset_and_reserve(tmp_path):
    clock = [NOW]
    quota = QuotaManager(tmp_path / "quota.sqlite", "DEFAULT", 10, 10, 10, clock=lambda: clock[0])
    for _ in range(9):
        quota.consume("search")
    with pytest.raises(QuotaStopped):
        quota.consume("search")
    clock[0] += timedelta(days=1)
    quota.consume("search")
    assert quota.usage()["search_calls_used"] == 1


def test_preflight_rejection_performs_no_network(tmp_path):
    manager = ExperimentManager(
        tmp_path,
        ApiProfiles(tmp_path / "absent", {"YOUTUBE_API_KEY_DEFAULT": "fake"}),
        lambda *a, **k: pytest.fail("client should not be created"),
    )
    config = ExperimentConfig(Mode.TREND, (CollectionRequest(CandidateTopic.named("AI")),), search_budget=1)
    with pytest.raises(QuotaStopped):
        manager.run(config)
    assert not (tmp_path / "experiments").exists()


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_fatal_errors_and_retry_bounds(status):
    calls = []

    class Response:
        ok = False
        status_code = status

        def json(self):
            return {"error": {"errors": [{"reason": "quotaExceeded" if status == 403 else "unknown"}]}}

    class Session:
        def get(self, *a, **k):
            calls.append(1)
            return Response()

    client = YouTubeClient("synthetic", session=Session(), sleeper=lambda _: None)
    with pytest.raises(YouTubeApiError) as error:
        client.get("search", {})
    assert len(calls) == (3 if status in (429, 500) else 1)
    assert "synthetic" not in str(error.value)
    if status == 403:
        assert isinstance(error.value, YouTubeQuotaExceeded)


def test_ewma_notebook_example_and_gap_resume():
    engine = TrendEngine()
    first = engine.process(make_trend_bundle(12, 20))
    second = engine.process(make_trend_bundle(13, 40))
    expected = 40 * (1 - 2 ** (-0.5)) + 20 * 2 ** (-0.5)
    assert first["ewma"] == 20
    assert second["ewma"] == pytest.approx(expected)
    assert second["velocity"] == pytest.approx(math.log1p(expected) - math.log1p(20))
    engine.process(make_trend_bundle(14, 0, Status.FAILED))
    resumed = engine.process(make_trend_bundle(15, 20))
    assert resumed["velocity"] is None and resumed["acceleration"] is None


def test_window_coverage_and_repeat_poll():
    engine = TrendEngine()
    bundle = make_trend_bundle(12, 20)
    short = replace(bundle, metadata=replace(bundle.metadata, effective_from="2026-09-17T11:30:00Z"))
    result = engine.process(short)
    assert result["youtube_activity_rate"] is None
    assert engine.process(short) is None


def test_historical_flow_no_baselines_or_fake_counters(tmp_path):
    clients = []

    def factory(*args, **kwargs):
        client = RunClient()
        clients.append(client)
        return client

    request = CollectionRequest(
        CandidateTopic.named("AI"),
        window_mode="STATIC",
        requested_from="2026-09-01T00:00:00Z",
        requested_to="2026-09-18T00:00:00Z",
    )
    config = ExperimentConfig(Mode.HISTORICAL, (request,))
    experiment = ExperimentManager(
        tmp_path, ApiProfiles(tmp_path / "absent", {"YOUTUBE_API_KEY_DEFAULT": "fake"}), factory
    ).run(config)
    assert all(r not in ("channels", "playlistItems") for r, _ in clients[0].calls)
    rows = read_jsonl(experiment / "results" / request.topic.topic_id / "historical.jsonl")
    assert all(r["velocity"] is None and r["engagement"] is None for r in rows)


def test_product_mode_report_and_selection(tmp_path):
    requests = tuple(CollectionRequest(CandidateTopic.named(n)) for n in ("AI", "Gaming"))
    config = ExperimentConfig(Mode.PRODUCT, requests, duration_minutes=0.001)
    experiment = ExperimentManager(
        tmp_path, ApiProfiles(tmp_path / "absent", {"YOUTUBE_API_KEY_DEFAULT": "fake"}), RunClient
    ).run(config)
    report = read_json(experiment / "product_report.json")
    assert len(report["candidate_topics"]) == 2
    assert len(report["topics_monitored"]) == 1
    assert sum(report["selection_allocation"].values()) == 1


def test_replay_rejects_missing_baseline(tmp_path):
    request = CollectionRequest(CandidateTopic.named("AI"))
    config = ExperimentConfig(Mode.GAP, (request,), duration_minutes=0.001)
    experiment = ExperimentManager(
        tmp_path, ApiProfiles(tmp_path / "absent", {"YOUTUBE_API_KEY_DEFAULT": "fake"}), RunClient
    ).run(config)
    next((experiment / "enrichment").glob("*.json")).unlink()
    with pytest.raises(ValueError, match="Cannot read JSON"):
        ReplayService(tmp_path).run(experiment)
