"""Regression fixtures and edge-case test suite for YouTube Trend Engine specification.

Verifies mathematical alignment with CreatorRadar_YouTube_TrendEngine_SPEC.ipynb.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from research.core.domain import (
    CandidateTopic,
    CollectionBundle,
    CollectionMetadata,
    CollectionRequest,
    ExperimentConfig,
    Mode,
    Status,
    VideoIdentity,
    VideoObservation,
)
from research.core.time import iso
from research.metrics.baselines import HistoricalBaseline
from research.metrics.tracking import TrackedVideoRegistry
from research.metrics.trend import TrendConfig, TrendEngine
from research.orchestration.replay import ReplayService
from research.storage.db import DatabaseStorageAdapter

# ======================================================================
# 1. SPECIFICATION REGRESSION FIXTURES (CELL 27 & 28)
# ======================================================================


def test_spec_fixture_ewma():
    """Verify EWMA formula against Cell 27 fixture: r_prev=20, x=40, H=2h, dt=1h -> r ≈ 25.86."""
    r_prev = 20.0
    x = 40.0
    h = 2.0
    dt = 1.0
    alpha = 1.0 - 2.0 ** (-dt / h)
    r = alpha * x + (1.0 - alpha) * r_prev
    assert r == pytest.approx(25.857864376, rel=1e-5)


def test_spec_fixture_velocity():
    """Verify Velocity formula against Cell 27 fixture: r_prev=20, r=30, dt=1h -> v ≈ 0.389 h^-1."""
    r_prev = 20.0
    r = 30.0
    dt = 1.0
    v = (math.log1p(r) - math.log1p(r_prev)) / dt
    expected = math.log(31.0) - math.log(21.0)
    assert v == pytest.approx(expected)
    assert v == pytest.approx(0.389464, rel=1e-4)


def test_spec_fixture_acceleration():
    """Verify Acceleration formula against Cell 27 fixture: v_prev=0.20, v=0.389, dt=1h -> a ≈ 0.189 h^-2."""
    v_prev = 0.20
    v = 0.389
    dt = 1.0
    a = (v - v_prev) / dt
    assert a == pytest.approx(0.189)


def test_spec_fixture_growth():
    """Verify Growth G_YT against Cell 27 fixture: v=0.18, v_scale=0.24 -> G = 0.75."""
    v = 0.18
    v_scale = 0.24
    growth = min(max(max(v, 0.0) / v_scale, 0.0), 1.0)
    assert growth == pytest.approx(0.75)


def test_spec_fixture_burst():
    """Verify Burst Z_YT against Cell 27 fixture: z=3.2 -> Z = 3.2 / 4.0 = 0.80."""
    z = 3.2
    burst = min(max(max(z, 0.0) / 4.0, 0.0), 1.0)
    assert burst == pytest.approx(0.80)


def test_spec_fixture_engagement():
    """Verify Engagement E_YT against Cell 27 fixture: q_like=0.80, q_comment=0.70 -> E = 0.77."""
    q_like = 0.80
    q_comment = 0.70
    engagement = 0.70 * q_like + 0.30 * q_comment
    assert engagement == pytest.approx(0.77)


def test_spec_fixture_support():
    """Verify Effective Support N against Cell 27 fixture: n_eff=40 -> N = 40/50 = 0.80."""
    n_eff = 40.0
    support = min(1.0, n_eff / 50.0)
    assert support == pytest.approx(0.80)


def test_spec_fixture_duration():
    """Verify Observation Duration O against Cell 27 fixture: T_eff=2h -> O = 2/3 ≈ 0.667."""
    t_eff = 2.0
    duration = min(t_eff / 3.0, 1.0)
    assert duration == pytest.approx(2.0 / 3.0)


def test_spec_fixture_composite_trend_score():
    """Verify composite YouTube Trend Score:

    With engagement: 100 * (0.45*G + 0.25*Z + 0.15*E + 0.10*B + 0.05*P)
    """
    g = 0.75
    z = 0.80
    e = 0.77
    b = 0.60
    p = 1.0 / 3.0  # Qualified YouTube single platform
    score = 100.0 * (0.45 * g + 0.25 * z + 0.15 * e + 0.10 * b + 0.05 * p)
    expected = 100.0 * (0.3375 + 0.2000 + 0.1155 + 0.0600 + 0.05 / 3.0)
    assert score == pytest.approx(expected)
    assert score == pytest.approx(72.9666667, rel=1e-5)

    # Without engagement: 100 * (0.55*G + 0.30*Z + 0.10*B + 0.05*P)
    score_no_e = 100.0 * (0.55 * g + 0.30 * z + 0.10 * b + 0.05 * p)
    expected_no_e = 100.0 * (0.55 * 0.75 + 0.30 * 0.80 + 0.10 * 0.60 + 0.05 / 3.0)
    assert score_no_e == pytest.approx(expected_no_e)
    assert score_no_e == pytest.approx(72.9166667, rel=1e-5)


# ======================================================================
# 2. TREND ENGINE INTEGRATED PIPELINE & CANONICAL OUTPUT TESTS
# ======================================================================


def make_bundle(
    start_hour: int = 12,
    video_count: int = 20,
    creators_count: int = 10,
    status: str = Status.COMPLETE,
    topic: CandidateTopic | None = None,
) -> CollectionBundle:
    """Create a synthetic discovery bundle with known timestamps and creators."""
    base = datetime(2026, 9, 22, 0, 0, 0, tzinfo=UTC)
    from_time = base + timedelta(hours=start_hour - 1)
    to_time = base + timedelta(hours=start_hour)
    topic_obj = topic or CandidateTopic.named("synthetic_topic")

    discovery = []
    observations = []
    for i in range(video_count):
        vid = f"vid_{start_hour}_{i}"
        cid = f"chan_{i % creators_count}"
        pub = from_time + timedelta(seconds=(i * 3500) / max(1, video_count))
        ident = VideoIdentity(vid, cid, iso(pub))
        discovery.append(ident)
        obs = VideoObservation(ident, iso(to_time), 1000 + i * 50, 50 + i, 10 + i)
        observations.append(obs)

    metadata = CollectionMetadata(
        batch_id=f"batch_{start_hour}",
        timestamp=iso(to_time),
        query="synthetic",
        requested_from=iso(from_time),
        requested_to=iso(to_time),
        effective_from=iso(from_time),
        effective_to=iso(to_time),
        window_mode="STATIC",
        rolling_window_hours=1.0,
        page_size=50,
        max_pages=1,
        pages_requested=1,
        pages_received=1,
        items_received=len(discovery),
        unique_video_count=len(discovery),
        duplicates_removed=0,
        api_profile_name="DEFAULT",
        started_at=iso(from_time),
        finished_at=iso(to_time),
        duration_ms=100.0,
        status=status,
    )
    return CollectionBundle(
        topic=topic_obj,
        metadata=metadata,
        discovery=tuple(discovery),
        observations=tuple(observations),
    )


def test_trend_engine_calibrated_pipeline_with_baseline():
    """Verify that TrendEngine loads and computes with a calibrated HistoricalBaseline."""
    baseline = HistoricalBaseline(
        baseline_version="youtube_baseline_test_v1",
        status="READY",
        v_scale_yt=0.24,
        median_log_activity=math.log1p(15.0),
        mad_log_activity=0.4,
        like_reaction_rates=[0.02 * (i + 1) for i in range(20)],
        comment_reaction_rates=[0.005 * (i + 1) for i in range(20)],
        creator_counts=[float(i + 1) for i in range(20)],
        views_per_hour_by_age={"0_24h": [10.0 * (i + 1) for i in range(20)]},
    )
    engine = TrendEngine(TrendConfig(), baseline=baseline)

    # First window (hour 12)
    row1 = engine.process(make_bundle(12, video_count=20, creators_count=10))
    assert row1["youtube_activity_count"] == 20
    assert row1["ewma"] == 20.0
    assert row1["velocity"] is None
    assert row1["growth_G_YT"] is None

    # Second window (hour 13) with 20 distinct creators satisfying min_support=20
    row2 = engine.process(make_bundle(13, video_count=35, creators_count=20))
    assert row2["youtube_activity_count"] == 35
    assert row2["velocity"] > 0.0
    assert row2["growth_G_YT"] is not None
    assert 0.0 <= row2["growth_G_YT"] <= 1.0
    assert row2["burst_Z_YT"] is not None
    assert row2["breadth_B_YT"] is not None
    assert row2["platform_confirmation_P"] == pytest.approx(1.0 / 3.0)
    assert row2["youtube_trend_score"] is not None
    assert row2["trend_formula_version"] == "youtube_research_trend_noE_v1"

    # Third contiguous window (hour 14) with 20 creators
    row3 = engine.process(make_bundle(14, video_count=50, creators_count=20))
    assert row3["gate_pass"] is True
    assert row3["youtube_trend_score_public"] is not None
    assert row3["youtube_trend_score_public"] == row3["youtube_trend_score"]
    assert row3["lifecycle_state"] in ("RISING", "BREAKOUT")


# ======================================================================
# 3. EDGE CASES TESTS (SECTION 25)
# ======================================================================


def test_edge_case_negative_counter_deltas_are_corrections():
    """Negative counter deltas are platform audit corrections, not negative engagement."""
    registry = TrackedVideoRegistry()
    ident = VideoIdentity("v1", "c1", "2026-09-22T10:00:00Z")
    obs1 = VideoObservation(ident, "2026-09-22T12:00:00Z", views=5000, likes=200, comments=50)
    obs2 = VideoObservation(ident, "2026-09-22T13:00:00Z", views=4800, likes=190, comments=45)  # Audit drop

    deltas1 = registry.observe((obs1,))
    assert len(deltas1) == 0
    deltas2 = registry.observe((obs2,))
    assert len(deltas2) == 1
    d = deltas2[0]
    assert d.correction is True
    assert d.delta_views == -200
    assert d.views_per_hour is None
    assert d.like_rate is None  # Dropped from engagement


def test_edge_case_insufficient_delta_views():
    """Observations with delta_views < 100 must be marked unavailable for engagement."""
    registry = TrackedVideoRegistry()
    ident = VideoIdentity("v1", "c1", "2026-09-22T10:00:00Z")
    obs1 = VideoObservation(ident, "2026-09-22T12:00:00Z", views=1000, likes=50, comments=10)
    obs2 = VideoObservation(
        ident, "2026-09-22T13:00:00Z", views=1050, likes=52, comments=11
    )  # delta = 50 < 100

    registry.observe((obs1,))
    deltas = registry.observe((obs2,))
    assert len(deltas) == 1
    assert deltas[0].delta_views == 50
    assert deltas[0].like_rate is None  # Ineligible for reaction rate


def test_edge_case_one_creator_many_videos():
    """One creator publishing multiple videos must not artificially inflate distinct creator count."""
    engine = TrendEngine()
    # 50 videos published by only 1 creator
    bundle = make_bundle(12, video_count=50, creators_count=1)
    row = engine.process(bundle)
    assert row["sampled_video_count"] == 50
    assert row["unique_creators"] == 1
    assert row["distinct_creator_count"] == 1


def test_edge_case_missing_window_api_outage():
    """An API outage reduces completeness and leaves missing data as null, not zero."""
    engine = TrendEngine()
    engine.process(make_bundle(12, 20, 5))
    failed = make_bundle(13, 0, 0, status=Status.FAILED)
    row = engine.process(failed)
    assert row["youtube_activity_count"] is None
    assert row["youtube_activity_rate"] is None
    assert row["velocity"] is None
    assert row["research_gate"] == "INCOMPLETE_OR_CAPPED_COLLECTION"
    assert row["gate_pass"] is False
    assert row["gate_fail_reasons"] == ["INCOMPLETE_OR_CAPPED_COLLECTION"]


def test_edge_case_gate_failure_preserves_internal_score():
    """When gate fails, internal score is preserved, but public score is None."""
    baseline = HistoricalBaseline(
        status="READY",
        v_scale_yt=0.24,
        median_log_activity=2.0,
        mad_log_activity=0.5,
        creator_counts=[float(i + 1) for i in range(20)],
    )
    engine = TrendEngine(TrendConfig(min_support=100.0), baseline=baseline)  # Impossible support

    engine.process(make_bundle(12, 20, 5))
    row = engine.process(make_bundle(13, 30, 5))
    assert row["youtube_trend_score"] is not None  # Calculated internally
    assert row["youtube_trend_score_public"] is None  # Hidden publicly
    assert row["gate_pass"] is False
    assert any("insufficient_support" in r for r in row["gate_fail_reasons"])


def test_edge_case_large_polling_gap_resets_derivatives():
    """A gap exceeding max_gap_windows resets derivative continuity."""
    engine = TrendEngine(TrendConfig(max_gap_windows=2.0))
    engine.process(make_bundle(12, 20, 5))
    engine.process(make_bundle(13, 30, 5))
    # Gap of 5 hours (hour 13 to hour 18)
    resumed = engine.process(make_bundle(18, 40, 5))
    assert resumed["velocity"] is None
    assert resumed["acceleration"] is None


def test_dynamic_media_velocity_v_yt_percentile():
    """CounterDelta computes V_YT percentile against historical same-age baseline cohort."""
    baseline = HistoricalBaseline(
        status="READY",
        views_per_hour_by_age={"0_24h": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]},
    )
    registry = TrackedVideoRegistry()
    ident = VideoIdentity("v1", "c1", "2026-09-22T08:00:00Z")
    obs1 = VideoObservation(ident, "2026-09-22T10:00:00Z", views=1000, likes=50, comments=10)
    obs2 = VideoObservation(ident, "2026-09-22T11:00:00Z", views=1050, likes=55, comments=12)  # 50 views/h

    registry.observe((obs1,), baseline=baseline)
    deltas = registry.observe((obs2,), baseline=baseline)
    assert len(deltas) == 1
    d = deltas[0]
    assert d.views_per_hour == 50.0
    assert d.age_bucket == "0_24h"
    # In cohort [10..100], count <= 50 is 5 items out of 10 -> percentile = 0.50
    assert d.media_velocity_V_YT == pytest.approx(0.50)


# ======================================================================
# 4. REPLAY DETERMINISM TEST (SECTION 23 & 26)
# ======================================================================


def test_replay_trend_metrics_determinism():
    """Replay must reproduce exact Trend metrics with zero network calls."""
    db = DatabaseStorageAdapter()
    topic = CandidateTopic.named("AI_Test")
    db.seed_category("tech_ai", "AI & Technology")
    db.register_topic(topic.topic_id, topic.canonical_name, "ai", category_id="tech_ai")

    b1 = make_bundle(12, 20, 10, topic=topic)
    b2 = make_bundle(13, 30, 10, topic=topic)
    db.save_bundle(b1, operation="discovery")
    db.save_bundle(b2, operation="discovery")

    trend_engine = TrendEngine(TrendConfig())
    registry = TrackedVideoRegistry(limit=100)
    for b in (b1, b2):
        deltas = registry.observe(b.observations, baseline=trend_engine.baseline)
        trend_engine.engagement(deltas)
        row = trend_engine.process(b)
        db.save_trend_metric(topic.topic_id, b.metadata.batch_id, b.metadata.timestamp, row)

    rows1 = db.get_trend_history(topic.topic_id)
    assert len(rows1) == 2

    # Run Replay
    replay = ReplayService(db)
    rows2 = replay.replay_topic(topic.topic_id)

    assert len(rows1) == len(rows2) == 2
    for r1, r2 in zip(rows1, rows2):
        assert r1["youtube_activity_count"] == r2["youtube_activity_count"]
        assert r1["ewma"] == r2["ewma"]
        assert r1["velocity"] == r2["velocity"]
        assert r1["growth_g_yt"] == r2["growth_G_YT"]
        assert r1["burst_z_yt"] == r2["burst_Z_YT"]
        assert r1["youtube_trend_score"] == r2["youtube_trend_score"]
        assert r1["lifecycle_state"] == r2["lifecycle"]


def test_trend_engine_handles_minimum_video_age_and_effective_to_alignment():
    """Publication bucket must align with effective_to (respecting minimum_video_age_hours)."""
    engine = TrendEngine()
    # Batch 1 collected at 14:00 with 1 hour age lag: effective_to is 13:00 (videos from 12:00 to 13:00)
    b1 = make_bundle(13, 20, 5)
    b1_shifted = CollectionBundle(
        topic=b1.topic,
        metadata=replace(
            b1.metadata,
            timestamp=iso(utc(b1.metadata.timestamp) + timedelta(hours=1)),  # Collected at 14:00
            effective_from=b1.metadata.effective_from,
            effective_to=b1.metadata.effective_to,  # 13:00
        ),
        discovery=b1.discovery,
        observations=b1.observations,
    )
    r1 = engine.process(b1_shifted)
    assert r1 is not None
    assert r1["youtube_activity_rate"] is not None
    assert r1["ewma"] is not None

    # Batch 2 collected at 15:00: effective_to is 14:00 (videos from 13:00 to 14:00)
    b2 = make_bundle(14, 30, 5)
    b2_shifted = CollectionBundle(
        topic=b2.topic,
        metadata=replace(
            b2.metadata,
            timestamp=iso(utc(b2.metadata.timestamp) + timedelta(hours=1)),  # Collected at 15:00
            effective_from=b2.metadata.effective_from,
            effective_to=b2.metadata.effective_to,  # 14:00
        ),
        discovery=b2.discovery,
        observations=b2.observations,
    )
    r2 = engine.process(b2_shifted)
    assert r2 is not None
    assert r2["velocity"] is not None
    assert r2["ewma"] is not None


def test_trend_engine_calculates_when_truncated_by_max_pages():
    """Collections capped by max_pages (truncated=True) are valid samples and must calculate metrics."""
    engine = TrendEngine()
    b1 = make_bundle(12, 20, 5)
    b1_trunc = CollectionBundle(
        topic=b1.topic,
        metadata=replace(b1.metadata, truncated=True),
        discovery=b1.discovery,
        observations=b1.observations,
    )
    r1 = engine.process(b1_trunc)
    assert r1 is not None
    assert r1["youtube_activity_rate"] is not None

    b2 = make_bundle(13, 30, 5)
    b2_trunc = CollectionBundle(
        topic=b2.topic,
        metadata=replace(b2.metadata, truncated=True),
        discovery=b2.discovery,
        observations=b2.observations,
    )
    r2 = engine.process(b2_trunc)
    assert r2 is not None
    assert r2["velocity"] is not None
    assert r2["ewma"] is not None
    assert r2["youtube_trend_score"] is not None


