import json
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

import pytest

from research.application import ExperimentManager
from research.discovery import YouTubeDiscoveryCollector
from research.domain import (
    CandidateTopic,
    CollectionRequest,
    ExperimentConfig,
    KnownEvent,
    Mode,
    Status,
    VideoIdentity,
    VideoObservation,
    iso,
)
from research.evaluation import EventEvaluator, HistoricalAnalyzer
from research.gap import GapEnricher
from research.profiles import ApiProfiles
from research.replay import ReplayService
from research.scheduling import TopicScheduler
from research.storage import TopicWorkspaceManager, read_json, read_jsonl
from research.tracking import TrackedVideoRegistry
from research.trend import TrendEngine, percentile
from tests.test_research_ingestion import NOW, FakeClient


class RunClient(FakeClient):
    def __init__(self, *args, **kwargs):
        super().__init__()

    def get(self, resource, params):
        response = super().get(resource, params)
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

    config = ExperimentConfig(mode, (CollectionRequest(CandidateTopic.named("AI")),), duration_minutes=0.001)
    manager = ExperimentManager(
        tmp_path, ApiProfiles(tmp_path / ".env", {"YOUTUBE_API_KEY_DEFAULT": "fake"}), factory
    )
    experiment = manager.run(config)
    resources = [r for r, _ in clients[0].calls]
    assert resources.count("search") == 1
    assert ("channels" in resources) == has_gap
    topic = config.requests[0].topic.topic_id
    assert (experiment / "results" / topic / "gap.jsonl").exists() == has_gap
    assert (experiment / "results" / topic / "trend.jsonl").exists() == has_trend
    raw_before = {str(p): p.read_bytes() for p in (tmp_path / "data" / "topics").rglob("raw/*.json")}
    with patch("requests.Session.get", side_effect=AssertionError("Replay touched network")):
        replay = ReplayService(tmp_path).run(experiment)
    for kind in ("gap", "trend"):
        path = experiment / "results" / topic / (kind + ".jsonl")
        if path.exists():
            assert read_jsonl(path) == read_jsonl(replay / "results" / topic / path.name)
    assert raw_before == {str(p): p.read_bytes() for p in (tmp_path / "data" / "topics").rglob("raw/*.json")}
    assert read_json(replay / "quota.json")["experiment_calls"] == 0


def test_cache_avoids_baseline_calls_and_trend_unfiltered(tmp_path):
    client = RunClient()
    request = CollectionRequest(CandidateTopic.named("AI"))
    bundle = YouTubeDiscoveryCollector(client).collect(request)
    config = ExperimentConfig(Mode.COMBINED, (request,))
    enricher = GapEnricher(client, tmp_path / "cache.json", config)
    assert len(enricher.enrich(bundle).videos) == 1
    calls = len(client.calls)
    assert len(enricher.enrich(bundle).videos) == 1
    assert len(client.calls) == calls
    strict = GapEnricher(client, tmp_path / "cache.json", replace(config, baseline_min=6))
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


def test_raw_tamper_and_topic_isolation(tmp_path):
    store = TopicWorkspaceManager(tmp_path)
    first, second = CandidateTopic.named("AI"), CandidateTopic.named("AI!")
    assert first.topic_id != second.topic_id
    config = ExperimentConfig(Mode.TREND, (CollectionRequest(first),))
    experiment = store.create(config)
    bundle = make_trend_bundle()
    store.save_bundle(experiment, bundle)
    with pytest.raises(FileExistsError):
        store.save_bundle(experiment, bundle)
    path = next((tmp_path / "data").rglob("raw/*.json"))
    content = read_json(path)
    content["warnings"] = ["tampered"]
    path.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="checksum"):
        list(store.bundles(experiment))


def test_scheduler_deterministic_exploration():
    topics = tuple(CandidateTopic.named(name) for name in ("A", "B", "C"))
    first, second = TopicScheduler(topics, 0.3), TopicScheduler(topics, 0.3)
    costs = {t.topic_id: 1 for t in topics}
    left = [first.choose(costs) for _ in range(20)]
    right = [second.choose(costs) for _ in range(20)]
    assert left == right
    assert sum(x.reason == "exploration" for x in left) == 6
    assert len({x.topic_id for x in left}) == 3
