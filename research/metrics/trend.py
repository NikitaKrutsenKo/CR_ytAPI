"""Trend Engine: deterministic time-series components, derivatives, burst, and research scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import mean, median
from typing import Any

from research.core.domain import CollectionBundle, Status
from research.core.time import iso, utc
from research.metrics.baselines import BaselineStore, HistoricalBaseline, percentile, quantile
from research.metrics.tracking import CounterDelta


@dataclass(frozen=True)
class TrendConfig:
    """Configuration parameters for Trend Engine component calculation and research gates.

    Attributes:
        formula_version: Canonical Trend formula version identifier.
        source_panel_version: YouTube collection panel version.
        confidence_formula_version: Evidence confidence formula version.
        normalization_version: Historical baseline normalization version.
        window_hours: Length of fixed event-time publication buckets in hours.
        half_life_hours: Half-life period for EWMA temporal smoothing.
        slope_floor: Minimum baseline denominator floor for growth scaling.
        baseline_min_windows: Minimum required prior accepted windows before computing G/Z.
        baseline_limit: Rolling buffer size limit for historical baseline observations.
        max_gap_windows: Maximum missing bucket span before resetting derivative continuity.
        source_cap: Upper bound cap on confidence metric.
        rising_score: Threshold for transitioning into RISING lifecycle state.
        breakout_score: Threshold for transitioning into BREAKOUT lifecycle state.
        min_support: Minimum effective creator support required for research publish gate.
        min_creators: Minimum distinct origins required for research publish gate.
    """

    formula_version: str = "youtube_research_trend_v1"
    source_panel_version: str = "youtube_research_v1"
    confidence_formula_version: str = "youtube_research_confidence_v1"
    normalization_version: str = "youtube_baseline_research_v1"
    window_hours: float = 1.0
    half_life_hours: float = 2.0
    slope_floor: float = 0.05
    baseline_min_windows: int = 3
    baseline_limit: int = 168
    max_gap_windows: float = 2.0
    source_cap: float = 0.6
    rising_score: float = 50.0
    breakout_score: float = 75.0
    min_support: float = 20.0
    min_creators: int = 5

    def __post_init__(self) -> None:
        if any(
            not math.isfinite(v) or v <= 0
            for v in (self.window_hours, self.half_life_hours, self.slope_floor, self.max_gap_windows)
        ):
            raise ValueError("Invalid Trend time/scale configuration")
        if not 0 <= self.source_cap <= 1 or not 0 <= self.rising_score < self.breakout_score <= 100:
            raise ValueError("Invalid Trend caps or lifecycle thresholds")
        if (
            not 1 <= self.baseline_min_windows <= self.baseline_limit
            or self.min_support <= 0
            or self.min_creators < 1
        ):
            raise ValueError("Invalid Trend support or baseline size")


@dataclass
class TrendState:
    """Internal state of the Trend Engine for a single topic.

    Attributes:
        last_timestamp: End timestamp of the last processed bucket.
        last_success_time: Timestamp of last successful API collection.
        ewma: Smoothed activity rate.
        velocity: Current rate of activity growth.
        valid_duration_hours: Total hours of accepted contiguous observations.
        valid_windows: Count of valid accepted windows.
        contiguous_windows: Count of contiguous accepted windows without gap.
        expected_windows: Total expected collection windows.
        lifecycle: Inferred lifecycle state ('WATCHING', 'RISING', 'BREAKOUT', 'PEAK', 'COOLING', 'ARCHIVED').
        lifecycle_reason: Explanation of the most recent lifecycle transition.
        history: Bounded buffer of past window observations.
    """

    last_timestamp: str | None = None
    last_success_time: str | None = None
    ewma: float | None = None
    velocity: float | None = None
    valid_duration_hours: float = 0.0
    valid_windows: int = 0
    contiguous_windows: int = 0
    expected_windows: int = 0
    lifecycle: str = "WATCHING"
    lifecycle_reason: str = "Initial state"
    history: list[dict] = field(default_factory=list)


class TrendEnricher:
    """Accumulates unique publication records in fixed event-time buckets and tracks time coverage."""

    def __init__(self) -> None:
        self.identities: dict[str, Any] = {}
        self.coverage: list[tuple[float, float]] = []

    def ingest(self, bundle: CollectionBundle) -> None:
        """Ingest discovery observations and record valid window coverage."""
        for item in bundle.discovery:
            self.identities.setdefault(item.video_id, item)
        if bundle.metadata.status == Status.COMPLETE:
            self.coverage.append(
                (
                    utc(bundle.metadata.effective_from).timestamp(),
                    utc(bundle.metadata.effective_to).timestamp(),
                )
            )

    def window(self, end: datetime, hours: float) -> list[Any]:
        """Return all published videos within [end - hours, end)."""
        lower = end.timestamp() - hours * 3600
        return [
            v for v in self.identities.values() if lower <= utc(v.published_at).timestamp() < end.timestamp()
        ]

    def covered(self, end: datetime, hours: float) -> bool:
        """Verify continuous un-gapped collection coverage over [end - hours, end)."""
        cursor = end.timestamp() - hours * 3600
        target = end.timestamp()
        for start, stop in sorted(self.coverage):
            if stop <= cursor:
                continue
            if start > cursor:
                return False
            cursor = max(cursor, stop)
            if cursor >= target:
                return True
        return False

    def prune(self, end: datetime, hours: float = 48.0) -> None:
        """Prune video records older than a retention threshold."""
        lower = end.timestamp() - hours * 3600
        self.identities = {
            k: v for k, v in self.identities.items() if utc(v.published_at).timestamp() >= lower
        }
        self.coverage = [(a, b) for a, b in self.coverage if b >= lower]


class TrendEngine:
    """Deterministic YouTube Trend Engine implementing mathematical components: EWMA, G, Z, B, E, P."""

    def __init__(
        self,
        config: TrendConfig | None = None,
        baseline: HistoricalBaseline | None = None,
    ) -> None:
        self.config = config or TrendConfig()
        self.state = TrendState()
        self.enricher = TrendEnricher()
        self.baseline = baseline or BaselineStore().get(self.config.normalization_version)
        self.like_history: dict[str, list[float]] = {}
        self.comment_history: dict[str, list[float]] = {}
        self.pending_engagement: list[float] = []

    def engagement(self, deltas: list[CounterDelta]) -> float | None:
        """Compute aggregated engagement score E_YT across counter deltas.

        Formula:
            E_i = 0.70 * ECDF(like_rate_i) + 0.30 * ECDF(comment_rate_i)
            E_YT = mean(E_i)
        """
        values = []
        for delta in deltas:
            if delta.like_rate is None or delta.comment_rate is None or delta.correction:
                continue
            # ECDF percentile: check baseline first, then dynamic like_history
            ql = self.baseline.percentile_like(delta.like_rate)
            if ql is None:
                ql = percentile(delta.like_rate, self.like_history.get(delta.age_bucket, []))
            qc = self.baseline.percentile_comment(delta.comment_rate)
            if qc is None:
                qc = percentile(delta.comment_rate, self.comment_history.get(delta.age_bucket, []))
            if ql is not None and qc is not None:
                values.append(0.70 * ql + 0.30 * qc)
        for delta in deltas:
            if delta.correction:
                continue
            for history, value in (
                (self.like_history, delta.like_rate),
                (self.comment_history, delta.comment_rate),
            ):
                if value is not None:
                    history[delta.age_bucket] = (history.get(delta.age_bucket, []) + [value])[-1000:]
        result = mean(values) if values else None
        if result is not None:
            self.pending_engagement.append(result)
        return result

    def process(self, bundle: CollectionBundle) -> dict | None:
        """Process a collection bundle and compute trend metrics for the completed publication bucket."""
        self.enricher.ingest(bundle)
        config, state = self.config, self.state
        ref_time = getattr(bundle.metadata, "effective_to", None) or bundle.metadata.timestamp
        moment = utc(ref_time)
        seconds = config.window_hours * 3600
        end = moment.fromtimestamp(math.floor(moment.timestamp() / seconds) * seconds, moment.tzinfo)
        timestamp = iso(end)
        window_start = iso(end - timedelta(hours=config.window_hours))
        if state.last_timestamp and utc(state.last_timestamp) >= end:
            return None
        previous_time = utc(state.last_timestamp) if state.last_timestamp else None
        hours = (end - previous_time).total_seconds() / 3600 if previous_time else config.window_hours
        slots = max(1, round(hours / config.window_hours))
        state.expected_windows += slots
        items = self.enricher.window(end, config.window_hours)
        support = self.enricher.window(end, 24)
        creators = len({v.channel_id for v in items})
        origins = len({v.channel_id for v in support})
        n_eff = float(origins)
        valid = (
            bundle.metadata.status == Status.COMPLETE
            and self.enricher.covered(end, config.window_hours)
        )

        n_topic_yt = len(items) if valid else None
        n_eligible_yt = getattr(bundle.metadata, "n_eligible_yt", None)
        youtube_incidence = (
            10000.0 * n_topic_yt / n_eligible_yt
            if n_topic_yt is not None and n_eligible_yt and n_eligible_yt > 0
            else None
        )

        freshness = (
            1.0
            if valid
            else (
                2.0
                ** (
                    -max(
                        0.0,
                        (utc(bundle.metadata.finished_at) - utc(state.last_success_time)).total_seconds()
                        / 3600.0,
                    )
                    / config.half_life_hours
                )
                if state.last_success_time
                else 0.0
            )
        )
        completeness = (state.valid_windows + int(valid)) / max(1, state.expected_windows)

        row = {
            "timestamp": bundle.metadata.finished_at,
            "window_start": window_start,
            "window_end": timestamp,
            "topic": bundle.topic.canonical_name,
            "batch_id": bundle.metadata.batch_id,
            "signal_basis": "youtube_query_publication_activity",
            "source_panel_version": config.source_panel_version,
            "normalization_version": self.baseline.baseline_version,
            "confidence_formula_version": config.confidence_formula_version,
            "youtube_activity_count": n_topic_yt,
            "youtube_incidence": youtube_incidence,
            "n_eligible_yt": n_eligible_yt,
            "n_topic_yt": n_topic_yt,
            "raw_item_count": len(bundle.discovery),
            "deduplicated_item_count": len(items),
            "sampled_video_count": len(items),
            "youtube_activity_rate": len(items) / config.window_hours if valid else None,
            "unique_creators": creators,
            "distinct_creator_count": creators,
            "effective_sample_size": n_eff,
            "ewma": None,
            "youtube_activity_ewma": None,
            "youtube_incidence_raw": len(items) / config.window_hours if valid else None,
            "ewma_alpha": None,
            "ewma_half_life_hours": config.half_life_hours,
            "velocity": None,
            "acceleration": None,
            "previous_rate": state.ewma,
            "current_rate": None,
            "delta_hours": hours,
            "growth": None,
            "growth_G_YT": None,
            "v_scale_yt": None,
            "v_scale_version": self.baseline.baseline_version,
            "baseline_status": self.baseline.status,
            "burst": None,
            "burst_Z_YT": None,
            "raw_robust_z_yt": None,
            "baseline_median_log": None,
            "baseline_mad_log": None,
            "engagement": None,
            "engagement_E_YT": None,
            "breadth": None,
            "breadth_B_YT": None,
            "creator_count_percentile": None,
            "confidence_N": min(n_eff / 50.0, 1.0),
            "confidence_D": 0.5 * min(origins / 10.0, 1.0) + 0.5 * (1.0 / 3.0),
            "confidence_F": freshness,
            "freshness": freshness,
            "confidence_M": completeness,
            "completeness": completeness,
            "confidence_O": None,
            "observation_duration_O": None,
            "effective_observation_duration_hours": state.valid_duration_hours,
            "confidence_base": None,
            "cap_source": config.source_cap,
            "cap_duration": None,
            "cap_policy": None,
            "confidence": None,
            "youtube_evidence_confidence": None,
            "platform_confirmation_P": 0.0,
            "youtube_platform_qualifies": False,
            "qualification_reasons": ["unprocessed"],
            "trend_score": None,
            "youtube_trend_score": None,
            "youtube_research_score": None,
            "youtube_trend_score_public": None,
            "gate_pass": False,
            "gate_fail_reasons": ["INCOMPLETE_OR_CAPPED_COLLECTION"] if not valid else [],
            "lifecycle": state.lifecycle,
            "lifecycle_state": state.lifecycle,
            "lifecycle_reason": state.lifecycle_reason,
            "lifecycle_previous_state": state.lifecycle,
            "score_status": "UNAVAILABLE: production source panel absent",
            "research_gate": "INCOMPLETE_OR_CAPPED_COLLECTION" if not valid else "INSUFFICIENT_EVIDENCE",
        }
        if not valid:
            state.last_timestamp = timestamp
            state.velocity = None
            state.ewma = None
            state.contiguous_windows = 0
            self.pending_engagement.clear()
            return row

        rate = row["youtube_activity_rate"]
        (
            smoothed,
            velocity,
            acceleration,
            growth,
            burst,
            breadth,
            alpha,
            v_scale,
            baseline_status,
            raw_z,
            center,
            scale,
        ) = self._components(rate, hours, previous_time, creators)

        engagement = mean(self.pending_engagement) if self.pending_engagement else None
        self.pending_engagement.clear()
        state.valid_windows += 1
        state.contiguous_windows = state.contiguous_windows + 1 if slots == 1 else 1
        state.valid_duration_hours += config.window_hours
        duration = min(state.valid_duration_hours / 3.0, 1.0)

        conf_base = (
            0.30 * row["confidence_N"]
            + 0.20 * row["confidence_D"]
            + 0.20 * freshness
            + 0.20 * completeness
            + 0.10 * duration
        )
        confidence = min(conf_base, config.source_cap)

        row.update(
            ewma=smoothed,
            youtube_activity_ewma=smoothed,
            ewma_alpha=alpha,
            velocity=velocity,
            acceleration=acceleration,
            current_rate=smoothed,
            growth=growth,
            growth_G_YT=growth,
            v_scale_yt=v_scale,
            baseline_status=baseline_status,
            burst=burst,
            burst_Z_YT=burst,
            raw_robust_z_yt=raw_z,
            baseline_median_log=center,
            baseline_mad_log=scale,
            engagement=engagement,
            engagement_E_YT=engagement,
            breadth=breadth,
            breadth_B_YT=breadth,
            creator_count_percentile=breadth,
            confidence_O=duration,
            observation_duration_O=duration,
            effective_observation_duration_hours=state.valid_duration_hours,
            confidence_base=conf_base,
            confidence=confidence,
            youtube_evidence_confidence=confidence,
        )

        self._publish(
            row=row,
            origins=origins,
            n_eff=n_eff,
            growth=growth,
            burst=burst,
            breadth=breadth,
            engagement=engagement,
            velocity=velocity,
            confidence=confidence,
            completeness=completeness,
            freshness=freshness,
        )

        state.history.append({"ewma": smoothed, "velocity": velocity, "creators": creators})
        state.history = state.history[-config.baseline_limit :]
        state.last_timestamp = timestamp
        state.ewma = smoothed
        state.velocity = velocity
        state.last_success_time = bundle.metadata.finished_at
        self.enricher.prune(end)
        return row

    def _components(self, rate: float, hours: float, previous_time: datetime | None, creators: int) -> tuple:
        """Compute derivatives, growth, burst, and breadth against baseline and prior state."""
        config, state, baseline = self.config, self.state, self.baseline
        contiguous = (
            previous_time is not None
            and hours <= config.max_gap_windows * config.window_hours
            and state.velocity is not None
        )
        alpha = 1.0 - 2.0 ** (-hours / config.half_life_hours)
        smoothed = rate if state.ewma is None else alpha * rate + (1.0 - alpha) * state.ewma
        velocity = (
            (math.log1p(smoothed) - math.log1p(state.ewma)) / hours
            if previous_time
            and state.ewma is not None
            and hours <= config.max_gap_windows * config.window_hours
            else None
        )
        acceleration = (velocity - state.velocity) / hours if velocity is not None and contiguous else None

        prior = state.history
        enough_prior = len(prior) >= config.baseline_min_windows

        # Growth G_YT
        v_scale = baseline.v_scale(floor=config.slope_floor) if baseline.status == "READY" else None
        baseline_status = baseline.status
        if v_scale is None and enough_prior:
            positive = [r["velocity"] for r in prior if r.get("velocity") is not None and r["velocity"] > 0]
            v_scale = max(quantile(positive, 0.90) if positive else 0.0, config.slope_floor)
            baseline_status = "WARMUP"
        elif v_scale is None:
            v_scale = config.slope_floor
            baseline_status = "INSUFFICIENT_DATA"

        growth = (
            min(max(max(velocity or 0.0, 0.0) / v_scale, 0.0), 1.0)
            if velocity is not None and (enough_prior or baseline.status == "READY")
            else None
        )

        # Burst Z_YT
        raw_z = None
        burst = None
        center, scale = None, None
        if baseline.status == "READY":
            center, scale = baseline.robust_z_parameters()
        elif enough_prior:
            logs = [math.log1p(r["ewma"]) for r in prior if r.get("ewma") is not None]
            center = median(logs)
            scale = max(1.4826 * median(abs(v - center) for v in logs), 0.25)

        if center is not None and scale is not None:
            raw_z = (math.log1p(smoothed) - center) / scale
            burst = min(max(max(raw_z, 0.0) / 4.0, 0.0), 1.0)

        # Breadth B_YT
        breadth = None
        if baseline.status == "READY" and baseline.creator_counts:
            breadth = baseline.percentile_creators(creators)
        elif enough_prior:
            breadth = percentile(creators, [r["creators"] for r in prior if "creators" in r])

        return (
            smoothed,
            velocity,
            acceleration,
            growth,
            burst,
            breadth,
            alpha,
            v_scale,
            baseline_status,
            raw_z,
            center,
            scale,
        )

    def _publish(
        self,
        row: dict,
        origins: int,
        n_eff: float,
        growth: float | None,
        burst: float | None,
        breadth: float | None,
        engagement: float | None,
        velocity: float | None,
        confidence: float,
        completeness: float,
        freshness: float,
    ) -> None:
        """Evaluate evidence quality, publish gate, platform confirmation, and lifecycle."""
        config, state = self.config, self.state

        # Platform confirmation P_YT
        platform_qualifies = (
            n_eff >= config.min_support
            and origins >= config.min_creators
            and (velocity or 0.0) > 0.0
            and freshness >= 0.5
            and completeness >= 0.80
        )
        P_YT = (1.0 / 3.0) if platform_qualifies else 0.0
        qualification_reasons = []
        if n_eff < config.min_support:
            qualification_reasons.append(f"support_{n_eff:.0f}_lt_{config.min_support}")
        if origins < config.min_creators:
            qualification_reasons.append(f"origins_{origins}_lt_{config.min_creators}")
        if (velocity or 0.0) <= 0.0:
            qualification_reasons.append("non_positive_velocity")
        if freshness < 0.5:
            qualification_reasons.append(f"stale_{freshness:.2f}_lt_0.50")
        if completeness < 0.80:
            qualification_reasons.append(f"incomplete_{completeness:.2f}_lt_0.80")
        if platform_qualifies:
            qualification_reasons.append("youtube_qualifies")

        # YouTube Trend Score
        score = None
        formula_version = config.formula_version
        if growth is not None and burst is not None and breadth is not None:
            if engagement is not None:
                # Canonical formula with engagement
                score = 100.0 * (
                    0.45 * growth + 0.25 * burst + 0.15 * engagement + 0.10 * breadth + 0.05 * P_YT
                )
                formula_version = "youtube_research_trend_v1"
            else:
                # Canonical formula without engagement
                score = 100.0 * (0.55 * growth + 0.30 * burst + 0.10 * breadth + 0.05 * P_YT)
                formula_version = "youtube_research_trend_noE_v1"

        # Publish / Research Gate
        gate_fail_reasons = []
        if growth is None:
            gate_fail_reasons.append("growth_unavailable")
        if burst is None:
            gate_fail_reasons.append("burst_unavailable")
        if breadth is None:
            gate_fail_reasons.append("breadth_unavailable")
        if n_eff < config.min_support:
            gate_fail_reasons.append(f"insufficient_support_{n_eff:.0f}_lt_{config.min_support}")
        if origins < config.min_creators:
            gate_fail_reasons.append(f"insufficient_creators_{origins}_lt_{config.min_creators}")
        if state.contiguous_windows < 3:
            gate_fail_reasons.append(f"insufficient_contiguous_windows_{state.contiguous_windows}_lt_3")
        if confidence < 0.45:
            gate_fail_reasons.append(f"low_confidence_{confidence:.2f}_lt_0.45")
        if completeness < 0.80:
            gate_fail_reasons.append(f"low_completeness_{completeness:.2f}_lt_0.80")

        gate_pass = len(gate_fail_reasons) == 0 and score is not None
        public_score = score if gate_pass else None

        # Lifecycle State Evaluation
        previous_lifecycle = state.lifecycle
        lifecycle_reason = "Initial watching state"
        if score is not None:
            if score >= config.breakout_score and (velocity or 0.0) > 0.0:
                state.lifecycle = "BREAKOUT"
                lifecycle_reason = (
                    f"Breakout score {score:.1f} >= {config.breakout_score} with positive velocity"
                )
            elif previous_lifecycle in ("RISING", "BREAKOUT") and (velocity or 0.0) <= 0.0:
                state.lifecycle = "PEAK"
                lifecycle_reason = (
                    f"Peak plateau from {previous_lifecycle} with non-positive velocity {velocity or 0:.3f}"
                )
            elif score >= config.rising_score and (velocity or 0.0) > 0.0:
                state.lifecycle = "RISING"
                lifecycle_reason = f"Rising score {score:.1f} >= {config.rising_score} with positive velocity"
            elif (velocity or 0.0) < 0.0:
                state.lifecycle = "COOLING"
                lifecycle_reason = f"Cooling with negative velocity {velocity or 0:.3f}"
            else:
                state.lifecycle = "WATCHING"
                lifecycle_reason = f"Watching score {score:.1f} below threshold"
        state.lifecycle_reason = lifecycle_reason

        row.update(
            formula_version=formula_version,
            trend_formula_version=formula_version,
            platform_confirmation_P=P_YT,
            youtube_platform_qualifies=platform_qualifies,
            qualification_reasons=qualification_reasons,
            youtube_trend_score=score,
            youtube_research_score=score,
            youtube_trend_score_public=public_score,
            gate_pass=gate_pass,
            gate_fail_reasons=gate_fail_reasons,
            research_gate=(
                ("PASS_WITH_ENGAGEMENT" if engagement is not None else "PASS_NO_ENGAGEMENT_v1")
                if gate_pass
                else (gate_fail_reasons[0] if gate_fail_reasons else "INSUFFICIENT_EVIDENCE")
            ),
            lifecycle=state.lifecycle,
            lifecycle_state=state.lifecycle,
            lifecycle_reason=state.lifecycle_reason,
            lifecycle_previous_state=previous_lifecycle,
            score_status=(
                "READY" if gate_pass else ("CALCULATED_INTERNAL" if score is not None else "UNAVAILABLE")
            ),
        )
