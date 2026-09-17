from datetime import datetime, timezone

import pytest

from models.batch import CollectionInfo, Topic, YouTubeBatch
from models.metrics import MetricSnapshot
from models.raw_video import RawTopicVideo, RecentVideoStat
from storage.raw_batch_store import RawBatchStore
from storage.validator import ValidationError, validate_batch, validate_snapshot


def make_batch() -> YouTubeBatch:
    stats = [
        RecentVideoStat(f"recent-{i}", float(i + 1), 100 * (i + 1))
        for i in range(5)
    ]
    video = RawTopicVideo(
        video_id="topic-video",
        channel_id="channel",
        published_at=datetime.now(timezone.utc).isoformat(),
        views=1000,
        likes=20,
        comments=3,
        recent_video_count=5,
        recent_video_stats=stats,
    )
    return YouTubeBatch(
        schema_version="1.0",
        topic=Topic("topic", "Topic"),
        collection=CollectionInfo("2026-09-14T09:00:00Z", "batch-1", 1),
        videos=[video],
    )


def test_raw_batch_roundtrip(tmp_path):
    store = RawBatchStore(tmp_path / "raw.jsonl")
    batch = make_batch()
    store.append(batch)
    loaded = list(store.read_all())
    assert loaded == [batch]


def test_batch_rejects_too_few_recent_videos():
    batch = make_batch()
    broken = batch.videos[0]
    object.__setattr__(broken, "recent_video_count", 4)
    with pytest.raises(ValidationError):
        validate_batch(batch)


def test_batch_rejects_current_video_in_recent_stats():
    batch = make_batch()
    object.__setattr__(batch.videos[0].recent_video_stats[0], "video_id", "topic-video")
    with pytest.raises(ValidationError):
        validate_batch(batch)


def test_snapshot_bounded_metrics_are_checked():
    snapshot = MetricSnapshot(
        schema_version="1.0",
        timestamp="2026-09-14T09:00:00Z",
        topic="topic",
        batch_id="batch-1",
        batch_size=1,
        creator_count=1,
        qualifying_video_count=1,
        total_views=1000,
        total_likes=20,
        total_comments=3,
        total_engagement=23,
        mean_view_rate=10,
        median_view_rate=10,
        mean_creator_views_rate=5,
        median_creator_views_rate=5,
        mean_creator_authority=1,
        median_creator_authority=1,
        max_creator_authority=1,
        expected_views_rate_mean=5,
        expected_views_rate_median=5,
        delta_hours=0,
        decay_factor=1,
        topic_rate_batch=10,
        topic_rate=10,
        er_batch=0.1,
        er_norm=0.2,
        er_low=0,
        er_high=1,
        pr_batch=1,
        pr_norm=1.2,
        pr_low=0,
        pr_high=2,
        supply_batch=1,
        supply=1,
        supply_norm=0.3,
        supply_low=0,
        supply_high=2,
        demand_batch=0.4,
        demand=0.4,
        demand_norm=0.4,
        gap_score=0.28,
    )
    with pytest.raises(ValidationError):
        validate_snapshot(snapshot)
