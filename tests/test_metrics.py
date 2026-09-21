from __future__ import annotations

from research.core.models import CollectionInfo, RawTopicVideo, RecentVideoStat, Topic, YouTubeBatch
from research.metrics.calculators import MetricConfig, log_min_max_normalize
from research.metrics.gap import GapEngine as MetricEngine
from research.storage.state import MetricsState


def make_batch(timestamp: str = "2026-09-14T09:00:00Z", views: int = 1200) -> YouTubeBatch:
    videos = []
    for index in range(2):
        videos.append(
            RawTopicVideo(
                video_id=f"v{index}",
                channel_id=f"c{index}",
                published_at="2026-09-14T07:00:00Z",
                views=views + index * 100,
                likes=60,
                comments=10,
                recent_video_count=5,
                recent_video_stats=[
                    RecentVideoStat("r1", 2.0, 200),
                    RecentVideoStat("r2", 4.0, 360),
                    RecentVideoStat("r3", 6.0, 480),
                    RecentVideoStat("r4", 8.0, 560),
                    RecentVideoStat("r5", 10.0, 650),
                ],
            )
        )
    return YouTubeBatch(
        schema_version="1.0",
        topic=Topic("topic", "test"),
        collection=CollectionInfo(timestamp, "batch", len(videos)),
        videos=videos,
    )


def test_first_batch_initializes_state() -> None:
    engine = MetricEngine(MetricConfig())
    snapshot, state = engine.process(make_batch())

    assert state.initialized is True
    assert state.topic_rate > 0
    assert state.supply > 0
    assert 0 <= snapshot.er_norm <= 1
    assert 0 <= snapshot.pr_norm <= 1
    assert 0 <= snapshot.supply_norm <= 1
    assert 0 <= snapshot.demand_norm <= 1
    assert 0 <= snapshot.gap_score <= 1


def test_second_batch_uses_previous_state() -> None:
    engine = MetricEngine(MetricConfig())
    first_snapshot, state = engine.process(make_batch())
    second_snapshot, second_state = engine.process(
        make_batch("2026-09-14T10:00:00Z", views=2400), state
    )

    assert second_snapshot.timestamp != first_snapshot.timestamp
    assert second_state.last_update_time == second_snapshot.timestamp
    assert second_snapshot.topic_rate != first_snapshot.topic_rate


def test_log_normalization_is_clipped() -> None:
    assert log_min_max_normalize(0.01, 0.1, 10.0, 1e-6) == 0.0
    assert log_min_max_normalize(100.0, 0.1, 10.0, 1e-6) == 1.0
    middle = log_min_max_normalize(1.0, 0.1, 10.0, 1e-6)
    assert 0.0 < middle < 1.0


def test_snapshot_contains_full_dataset_metrics():
    batch = make_batch("2026-09-14T10:00:00+00:00")
    snapshot, _ = MetricEngine().process(batch, MetricsState())
    assert snapshot.total_views > 0
    assert snapshot.total_engagement >= 0
    assert snapshot.batch_id == batch.collection.batch_id
    assert snapshot.creator_count >= 1
    assert snapshot.topic_rate_batch > 0
    assert snapshot.mean_creator_views_rate > 0
    assert 0.0 <= snapshot.gap_score <= 1.0
