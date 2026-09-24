import concurrent.futures
from datetime import timedelta

import pytest

from research.api.quota import QuotaStopped
from research.core.domain import (
    CandidateTopic,
    CollectionBundle,
    CollectionMetadata,
    QuotaTelemetry,
    Status,
    VideoIdentity,
    VideoObservation,
)
from research.core.time import iso, now_utc
from research.storage.db import DatabaseStorageAdapter


def make_sample_bundle(topic_id: str = "minecraft_123", batch_id: str = "batch_001") -> CollectionBundle:
    topic = CandidateTopic(topic_id=topic_id, canonical_name="Minecraft", aliases=("mc", "minecraft game"))
    now = now_utc()
    metadata = CollectionMetadata(
        batch_id=batch_id,
        timestamp=iso(now),
        query="minecraft",
        requested_from=iso(now - timedelta(hours=2)),
        requested_to=iso(now),
        effective_from=iso(now - timedelta(hours=2)),
        effective_to=iso(now),
        window_mode="ROLLING",
        rolling_window_hours=2.0,
        page_size=50,
        max_pages=1,
        pages_requested=1,
        pages_received=1,
        items_received=1,
        unique_video_count=1,
        duplicates_removed=0,
        api_profile_name="DEFAULT",
        started_at=iso(now),
        finished_at=iso(now),
        duration_ms=120.0,
        status=Status.COMPLETE,
    )
    obs = (
        VideoObservation(
            identity=VideoIdentity("vid_1", "chan_1", iso(now - timedelta(hours=1)), "Video 1"),
            timestamp=iso(now),
            views=1500,
            likes=100,
            comments=10,
        ),
    )
    disc = (VideoIdentity("vid_1", "chan_1", iso(now - timedelta(hours=1)), "Video 1"),)
    telemetry = (
        QuotaTelemetry(
            endpoint="search",
            timestamp=iso(now),
            api_profile_name="DEFAULT",
            estimated_cost=100.0,
            status="SUCCESS",
        ),
    )
    return CollectionBundle(
        topic=topic,
        metadata=metadata,
        observations=obs,
        discovery=disc,
        telemetry=telemetry,
        warnings=(),
    )


def test_category_crud_and_stats(tmp_path):
    db_file = tmp_path / "test.db"
    adapter = DatabaseStorageAdapter(db_file)

    adapter.seed_category("gaming", "Gaming")
    adapter.seed_category("tech_ai", "AI & Technology")

    categories = adapter.list_categories()
    assert len(categories) == 2
    cat_ids = [c["id"] for c in categories]
    assert "gaming" in cat_ids
    assert "tech_ai" in cat_ids

    adapter.update_category_stats("gaming", aggregate_momentum=12.5, breakout_count=3)
    gaming = adapter.get_category("gaming")
    assert gaming is not None
    assert gaming["aggregate_momentum"] == 12.5
    assert gaming["breakout_count"] == 3


def test_topic_registration_and_retrieval(tmp_path):
    db_file = tmp_path / "test.db"
    adapter = DatabaseStorageAdapter(db_file)
    adapter.seed_category("gaming", "Gaming")

    adapter.register_topic(
        topic_id="minecraft_1",
        canonical_name="Minecraft",
        query="minecraft",
        category_id="gaming",
        aliases=("mc", "minecraft game"),
        cadence="REGULAR",
        priority=2.0,
    )

    topic = adapter.get_topic("minecraft_1")
    assert topic is not None
    assert topic.id == "minecraft_1"
    assert topic.canonical_name == "Minecraft"
    assert topic.category_id == "gaming"
    assert topic.aliases == ("mc", "minecraft game")
    assert topic.cadence == "REGULAR"
    assert topic.priority == 2.0


def test_atomic_leasing_lifecycle(tmp_path):
    db_file = tmp_path / "test.db"
    adapter = DatabaseStorageAdapter(db_file)
    adapter.seed_category("gaming", "Gaming")

    # Topic with priority 1.0
    adapter.register_topic(
        topic_id="topic_low",
        canonical_name="Low Priority",
        query="low",
        category_id="gaming",
        priority=1.0,
    )
    # Topic with priority 5.0
    adapter.register_topic(
        topic_id="topic_high",
        canonical_name="High Priority",
        query="high",
        category_id="gaming",
        priority=5.0,
    )

    # Worker 1 claims topic - should claim topic_high due to higher priority
    claimed = adapter.claim_due_topic(worker_id="worker_1", lease_duration_seconds=60)
    assert claimed is not None
    assert claimed.id == "topic_high"
    assert claimed.locked_by == "worker_1"

    # Worker 2 claims topic - should get topic_low since topic_high is currently locked
    claimed_2 = adapter.claim_due_topic(worker_id="worker_2", lease_duration_seconds=60)
    assert claimed_2 is not None
    assert claimed_2.id == "topic_low"
    assert claimed_2.locked_by == "worker_2"

    # No more due topics
    claimed_3 = adapter.claim_due_topic(worker_id="worker_3", lease_duration_seconds=60)
    assert claimed_3 is None

    # Worker 1 releases topic_high with next_discovery scheduled in future
    future = now_utc() + timedelta(hours=1)
    adapter.release_topic(
        topic_id="topic_high",
        worker_id="worker_1",
        next_discovery_at=future,
        next_tracking_at=future,
        lifecycle_state="RISING",
        lifecycle_reason="Score crossed threshold",
    )

    released = adapter.get_topic("topic_high")
    assert released.locked_by is None
    assert released.locked_until is None
    assert released.lifecycle_state == "RISING"


def test_stopped_topics_are_not_leased(tmp_path):
    db_file = tmp_path / "test.db"
    adapter = DatabaseStorageAdapter(db_file)
    adapter.seed_category("gaming", "Gaming")

    adapter.register_topic(
        topic_id="stopped_topic",
        canonical_name="Stopped Topic",
        query="stopped",
        category_id="gaming",
        cadence="STOPPED",
    )

    claimed = adapter.claim_due_topic(worker_id="worker_1")
    assert claimed is None


def test_concurrent_leasing_no_double_allocation(tmp_path):
    db_file = tmp_path / "test.db"
    adapter = DatabaseStorageAdapter(db_file)
    adapter.seed_category("gaming", "Gaming")

    # Register 10 topics
    for i in range(10):
        adapter.register_topic(
            topic_id=f"topic_{i}",
            canonical_name=f"Topic {i}",
            query=f"topic {i}",
            category_id="gaming",
            priority=float(i),
        )

    claimed_topics = []

    def claim_job(worker_idx: int):
        worker_adapter = DatabaseStorageAdapter(db_file)
        res = worker_adapter.claim_due_topic(worker_id=f"worker_{worker_idx}", lease_duration_seconds=300)
        return res.id if res else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(claim_job, i) for i in range(10)]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if res:
                claimed_topics.append(res)

    # Every topic must have been claimed at most once (no duplicates!)
    assert len(claimed_topics) == len(set(claimed_topics))
    assert len(claimed_topics) == 10


def test_raw_bundle_roundtrip_fidelity(tmp_path):
    db_file = tmp_path / "test.db"
    adapter = DatabaseStorageAdapter(db_file)
    adapter.register_topic("minecraft_123", "Minecraft", "minecraft")

    bundle = make_sample_bundle("minecraft_123", "batch_001")
    adapter.save_bundle(bundle, operation="discovery", status="COMPLETE")

    # Retrieve bundle and verify exact fidelity
    loaded = adapter.get_bundle("batch_001")
    assert loaded is not None
    assert loaded.metadata.batch_id == "batch_001"
    assert loaded.topic.canonical_name == "Minecraft"
    assert len(loaded.observations) == 1
    assert loaded.observations[0].views == 1500
    assert loaded.observations[0].identity.video_id == "vid_1"
    assert loaded.telemetry[0].endpoint == "search"


def test_metrics_persistence_and_queries(tmp_path):
    db_file = tmp_path / "test.db"
    adapter = DatabaseStorageAdapter(db_file)
    adapter.register_topic("minecraft_123", "Minecraft", "minecraft")
    bundle = make_sample_bundle("minecraft_123", "batch_001")
    adapter.save_bundle(bundle)

    now = now_utc()
    trend_row = {
        "youtube_trend_score": 82.5,
        "youtube_trend_score_public": 82.5,
        "youtube_activity_count": 45,
        "youtube_incidence": 0.85,
        "ewma": 14.2,
        "velocity": 3.1,
        "acceleration": 0.4,
        "growth_G_YT": 0.75,
        "burst_Z_YT": 1.2,
        "engagement_E_YT": 0.65,
        "breadth_B_YT": 0.9,
        "platform_confirmation_P": 0.333,
        "youtube_evidence_confidence": 0.58,
        "gate_pass": True,
        "lifecycle_state": "BREAKOUT",
    }
    adapter.save_trend_metric("minecraft_123", "batch_001", now, trend_row)

    latest_trend = adapter.get_latest_trend("minecraft_123")
    assert latest_trend is not None
    assert latest_trend["youtube_trend_score"] == 82.5
    assert latest_trend["gate_pass"] == 1
    assert latest_trend["lifecycle_state"] == "BREAKOUT"
    assert latest_trend["details"]["burst_Z_YT"] == 1.2

    # Save gap metric
    adapter.save_gap_metric(
        topic_id="minecraft_123",
        batch_id="batch_001",
        timestamp=now,
        gap_score=0.48,
        demand_norm=0.8,
        supply_norm=0.4,
        creator_authority=1.5,
        details={"er_norm": 0.7, "pr_norm": 0.9},
    )

    latest_gap = adapter.get_latest_gap("minecraft_123")
    assert latest_gap is not None
    assert latest_gap["gap_score"] == 0.48
    assert latest_gap["details"]["er_norm"] == 0.7


def test_creator_baselines_cache_and_pruning(tmp_path):
    db_file = tmp_path / "test.db"
    adapter = DatabaseStorageAdapter(db_file)

    adapter.save_creator_baseline(
        channel_id="chan_valid",
        creator_rate=500.0,
        sample_count=10,
        details={"median_views": 10000},
        ttl_hours=1.0,
    )
    adapter.save_creator_baseline(
        channel_id="chan_expired",
        creator_rate=200.0,
        sample_count=5,
        details={"median_views": 2000},
        ttl_hours=-1.0,  # expired in past
    )

    valid = adapter.get_creator_baseline("chan_valid")
    assert valid is not None
    assert valid["creator_rate"] == 500.0
    assert valid["details"]["median_views"] == 10000

    expired = adapter.get_creator_baseline("chan_expired")
    assert expired is None

    deleted_count = adapter.prune_expired_creator_baselines()
    assert deleted_count >= 1


def test_quota_ledger_atomic_debits_and_safety_stop(tmp_path):
    db_file = tmp_path / "test.db"
    adapter = DatabaseStorageAdapter(db_file)

    # Initial state
    usage = adapter.get_quota_usage("DEFAULT")
    assert usage["search_calls_used"] == 0
    assert usage["search_remaining_percent"] == 100.0

    # Debit 2 search calls (budget 10, reserve 10% -> limit = 9)
    adapter.debit_quota(
        profile="DEFAULT",
        endpoint="search",
        units=2,
        search_budget=10,
        reserve_percent=10.0,
    )
    usage = adapter.get_quota_usage("DEFAULT")
    assert usage["search_calls_used"] == 2
    assert adapter.get_remaining_search_quota_percent("DEFAULT", search_budget=10) == 80.0

    # Debit 7 more calls -> total 9 (at limit)
    adapter.debit_quota(
        profile="DEFAULT",
        endpoint="search",
        units=7,
        search_budget=10,
        reserve_percent=10.0,
    )
    assert adapter.get_remaining_search_quota_percent("DEFAULT", search_budget=10) == 10.0

    # Attempting 1 more call exceeds safety limit (9) -> QuotaStopped
    with pytest.raises(QuotaStopped):
        adapter.debit_quota(
            profile="DEFAULT",
            endpoint="search",
            units=1,
            search_budget=10,
            reserve_percent=10.0,
        )
