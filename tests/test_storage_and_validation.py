from datetime import UTC, datetime

import pytest

from research.core.models import (
    CollectionInfo,
    MetricSnapshot,
    RawTopicVideo,
    RecentVideoStat,
    Topic,
    YouTubeBatch,
)
from research.storage.raw_batch_store import RawBatchStore
from research.storage.validator import ValidationError, validate_batch, validate_snapshot


def make_batch() -> YouTubeBatch:
    stats = [
        RecentVideoStat(f"recent-{i}", float(i + 1), 100 * (i + 1))
        for i in range(5)
    ]
    video = RawTopicVideo(
        video_id="topic-video",
        channel_id="channel",
        published_at=datetime.now(UTC).isoformat(),
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


def test_cleanup_temporary_files(tmp_path):
    from research.storage.io import cleanup_temporary_files

    real_file = tmp_path / "data" / "important.json"
    real_file.parent.mkdir(parents=True)
    real_file.write_text('{"status": "ok"}', encoding="utf-8")

    tmp_file_1 = tmp_path / "data" / "important.json.abc12345.tmp"
    tmp_file_1.write_text('{"status": "partial"}', encoding="utf-8")

    tmp_file_2 = tmp_path / "experiments" / "exp_01" / "bundle.json.def67890.tmp"
    tmp_file_2.parent.mkdir(parents=True)
    tmp_file_2.write_text('{"status": "stale"}', encoding="utf-8")

    removed = cleanup_temporary_files(tmp_path)
    assert removed == 2
    assert real_file.exists()
    assert not tmp_file_1.exists()
    assert not tmp_file_2.exists()


def test_write_and_read_json_compact_and_gzip(tmp_path):
    from research.storage.io import read_json, write_json

    payload = {"key": "value", "list": [1, 2, 3], "nested": {"sub": "data"}}

    # Human-friendly default test (indented)
    pretty_path = tmp_path / "pretty.json"
    write_json(pretty_path, payload)
    pretty_text = pretty_path.read_text(encoding="utf-8")
    assert "\n" in pretty_text
    assert "  \"key\": \"value\"" in pretty_text
    assert read_json(pretty_path) == payload

    # Compact test
    compact_path = tmp_path / "compact.json"
    write_json(compact_path, payload, compact=True)
    raw_text = compact_path.read_text(encoding="utf-8")
    assert "\n" not in raw_text
    assert read_json(compact_path) == payload

    # Gzip test with .gz suffix
    gz_path = tmp_path / "data.json.gz"
    write_json(gz_path, payload, compact=True)
    assert read_json(gz_path) == payload

    # Test magic byte auto-detection even when file lacks .gz extension
    magic_gz_path = tmp_path / "compressed_without_extension.bin"
    magic_gz_path.write_bytes(gz_path.read_bytes())
    assert read_json(magic_gz_path) == payload


def test_iter_jsonl_streaming(tmp_path):
    from research.storage.io import append_jsonl, iter_jsonl, read_jsonl

    path = tmp_path / "test.jsonl"
    append_jsonl(path, {"id": 1, "name": "alpha"})
    append_jsonl(path, {"id": 2, "name": "beta"})

    streamed = list(iter_jsonl(path))
    assert streamed == [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]
    assert read_jsonl(path) == streamed


def test_youtube_client_close_and_context_manager():
    from unittest.mock import MagicMock

    from research.api.client import YouTubeClient

    mock_session = MagicMock()
    client = YouTubeClient("fake-key", session=mock_session)
    client.close()
    mock_session.close.assert_called_once()

    mock_session_ctx = MagicMock()
    with YouTubeClient("fake-key", session=mock_session_ctx) as ctx_client:
        assert ctx_client is not None
    mock_session_ctx.close.assert_called_once()


def test_gap_enricher_prunes_cache(tmp_path):
    from research.metrics.gap import GapEnricher
    from research.storage.io import write_json

    cache_file = tmp_path / "creator_cache.json"
    dummy_cache = {
        "ch_1": {"last_refresh": "2026-09-01T00:00:00Z"},
        "ch_2": {"last_refresh": "2026-09-02T00:00:00Z"},
        "ch_3": {"last_refresh": "2026-09-03T00:00:00Z"},
        "ch_4": {"last_refresh": "2026-09-04T00:00:00Z"},
    }
    write_json(cache_file, dummy_cache)

    enricher = GapEnricher(None, cache_file, None, max_entries=2)
    assert len(enricher.cache) == 2
    assert "ch_3" in enricher.cache
    assert "ch_4" in enricher.cache
    assert "ch_1" not in enricher.cache
    assert "ch_2" not in enricher.cache

