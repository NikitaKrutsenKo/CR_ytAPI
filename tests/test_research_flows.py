import json
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest

from research.api.discovery import YouTubeDiscoveryCollector
from research.api.profiles import ApiProfiles
from research.core.domain import (
    CandidateTopic,
    CollectionRequest,
    ExperimentConfig,
    KnownEvent,
    Mode,
    Status,
    VideoIdentity,
    VideoObservation,
)
from research.core.time import iso, now_utc
from research.metrics.evaluation import EventEvaluator, HistoricalAnalyzer
from research.metrics.gap import GapEnricher
from research.metrics.tracking import TrackedVideoRegistry
from research.metrics.trend import TrendEngine, percentile
from research.orchestration.application import ExperimentManager
from research.orchestration.replay import ReplayService
from research.orchestration.scheduling import TopicScheduler
from research.storage.db import DatabaseStorageAdapter
from tests.test_research_ingestion import NOW, FakeClient


class RunClient(FakeClient):
    def __init__(self, *args, **kwargs):
        super().__init__()

    def get(self, resource, params):
        response = super().get(resource, params)
        for item in response.get("items", []):
            if "snippet" in item:
                item["snippet"]["publishedAt"] = iso(now_utc() - timedelta(hours=2))
        if resource == "search":
            response.pop("nextPageToken", None)
        return response


@pytest.mark.parametrize(
    "mode,has_gap,has_trend",
    [(Mode.GAP, True, False), (Mode.TREND, False, True), (Mode.COMBINED, True, True)],
)
def test_live_modes_shared_discovery_and_offline_replay(tmp_path, mode, has_gap, has_trend):
    clients = []

    def factory(*args, **kwargs):
        client = RunClient()
        clients.append(client)
        return client

    config = ExperimentConfig(
        mode, (CollectionRequest(CandidateTopic.named("AI")),), duration_hours=0.001 / 60
    )
    manager = ExperimentManager(
        tmp_path, ApiProfiles(tmp_path / ".env", {"YOUTUBE_API_KEY_DEFAULT": "fake"}), factory
    )
    manager.store.clean_tables()
    manager.store.seed_category("tech_ai", "AI & Technology")

    run_id = manager.run(config)
    assert run_id.startswith("exp_")

    resources = [r for r, _ in clients[0].calls]
    assert resources.count("search") == 1
    assert ("channels" in resources) == has_gap

    topic = config.requests[0].topic.topic_id
    latest_gap = manager.store.get_latest_gap(topic)
    latest_trend = manager.store.get_latest_trend(topic)

    assert (latest_gap is not None) == has_gap
    assert (latest_trend is not None) == has_trend

    # Test offline replay from database
    with patch("requests.Session.get", side_effect=AssertionError("Replay touched network")):
        replayed = ReplayService(manager.store).replay_topic(topic)
    if has_trend:
        assert len(replayed) >= 1


def test_cache_avoids_baseline_calls_and_trend_unfiltered():
    client = RunClient()
    request = CollectionRequest(CandidateTopic.named("AI"))
    bundle = YouTubeDiscoveryCollector(client).collect(request)
    config = ExperimentConfig(Mode.COMBINED, (request,))
    db = DatabaseStorageAdapter()
    enricher = GapEnricher(client, db, config)
    assert len(enricher.enrich(bundle).videos) == 1
    calls = len(client.calls)
    assert len(enricher.enrich(bundle).videos) == 1
    assert len(client.calls) == calls
    strict = GapEnricher(client, db, replace(config, baseline_min=6))
    assert len(strict.enrich(bundle).videos) == 0
    assert len(bundle.observations) == 1


def test_counter_deltas_missing_corrections_and_repetitions():
    identity = VideoIdentity("v", "c", "2026-09-17T09:00:00Z")
    registry = TrackedVideoRegistry()
    assert registry.observe((VideoObservation(identity, "2026-09-17T10:00:00Z", 100, 10, None),)) == []
    row = registry.observe((VideoObservation(identity, "2026-09-17T11:00:00Z", 300, 20, None),))[0]
    assert row.delta_views == 200 and row.views_per_hour == 200 and row.like_rate == 0.05
    assert row.comment_rate is None
    row = registry.observe((VideoObservation(identity, "2026-09-17T12:00:00Z", 290, 19, None),))[0]
    assert row.correction and row.views_per_hour is None


def make_trend_bundle(hour=12, count=30, status=Status.COMPLETE):
    request = CollectionRequest(CandidateTopic.named("AI"))
    bundle = YouTubeDiscoveryCollector(RunClient()).collect(request, now=NOW)
    end = NOW + timedelta(hours=hour - 12)
    identities = tuple(
        VideoIdentity(f"v{hour}_{i}", f"c{i}", iso(end - timedelta(minutes=30))) for i in range(count)
    )
    return replace(
        bundle,
        metadata=replace(
            bundle.metadata,
            timestamp=iso(end),
            finished_at=iso(end),
            effective_from=iso(end - timedelta(hours=24)),
            effective_to=iso(end),
            status=status,
        ),
        discovery=identities,
    )


def test_trend_causal_baselines_and_missing_not_zero():
    engine = TrendEngine()
    first = engine.process(make_trend_bundle())
    assert first["velocity"] is None and first["growth"] is None
    assert first["trend_score"] is None
    for hour in range(13, 17):
        result = engine.process(make_trend_bundle(hour, 30 + (hour - 12) * 10))
    assert result["growth"] is not None
    assert result["youtube_activity_count"] == 70
    assert result["trend_score"] is None
    missing = engine.process(make_trend_bundle(17, 0, Status.FAILED))
    assert missing["youtube_activity_count"] is None
    assert missing["youtube_research_score"] is None
    assert percentile(2, [1, 2, 3]) == 2 / 3


def test_historical_and_events_are_evaluation_only():
    bundle = make_trend_bundle()
    analyzer = HistoricalAnalyzer()
    rows = analyzer.publications(bundle)
    assert rows[0]["velocity"] is None and rows[0]["engagement"] is None
    event = KnownEvent(
        bundle.topic.topic_id,
        "Synthetic fixture",
        "control",
        "2026-09-17T12:00:00Z",
        "test",
        "https://example.com/fixture",
        "synthetic",
        "2026-09-17T12:00:00Z",
    )
    source = [{"timestamp": "2026-09-17T11:00:00Z", "lifecycle": "RISING"}]
    assert EventEvaluator().evaluate(source, event)["lead_time_to_event_hours"] == 1
    assert EventEvaluator().aligned(source, event)[0]["event_relative_hours"] == -1
    assert "event_time" not in TrendEngine.process.__code__.co_varnames


def test_raw_batch_and_topic_isolation():
    db = DatabaseStorageAdapter()
    first, second = CandidateTopic.named("AI"), CandidateTopic.named("AI!")
    assert first.topic_id != second.topic_id
    db.seed_category("tech_ai", "AI & Technology")
    db.register_topic(first.topic_id, first.canonical_name, "ai", category_id="tech_ai")
    db.register_topic(second.topic_id, second.canonical_name, "ai!", category_id="tech_ai")
    bundle = make_trend_bundle()
    db.save_bundle(bundle)
    loaded = db.get_bundle(bundle.metadata.batch_id)
    assert loaded is not None
    assert loaded.metadata.batch_id == bundle.metadata.batch_id


def test_scheduler_deterministic_exploration():
    topics = tuple(CandidateTopic.named(name) for name in ("A", "B", "C"))
    first, second = TopicScheduler(topics, 0.3), TopicScheduler(topics, 0.3)
    costs = {t.topic_id: 1 for t in topics}
    left = [first.choose(costs) for _ in range(20)]
    right = [second.choose(costs) for _ in range(20)]
    assert left == right
    assert sum(x.reason == "exploration" for x in left) == 6
    assert len({x.topic_id for x in left}) == 3
