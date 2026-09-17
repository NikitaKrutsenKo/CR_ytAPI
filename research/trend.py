from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean, median

from research.domain import CollectionBundle, Status, iso, utc
from research.tracking import CounterDelta


@dataclass(frozen=True)
class TrendConfig:
    """Versioned YouTube research adaptation; never impersonates the production panel."""

    formula_version: str = "youtube_activity_research_v1"
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

    def __post_init__(self):
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


def percentile(value: float, history: list[float]) -> float | None:
    """Empirical CDF of prior observations only; no current/future sample leakage."""
    return sum(v <= value for v in history) / len(history) if history else None


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low = int(position)
    return ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (position - low)


@dataclass
class TrendState:
    """State belongs to one topic, source plan and formula snapshot."""

    last_timestamp: str | None = None
    ewma: float | None = None
    velocity: float | None = None
    valid_duration_hours: float = 0.0
    valid_windows: int = 0
    contiguous_windows: int = 0
    expected_windows: int = 0
    lifecycle: str = "WATCHING"
    history: list[dict] = field(default_factory=list)


class TrendEnricher:
    """Collect unique publication identities in fixed event-time buckets, not poll counts."""

    def __init__(self):
        self.identities = {}
        self.coverage = []

    def ingest(self, bundle: CollectionBundle) -> None:
        for item in bundle.discovery:
            self.identities.setdefault(item.video_id, item)
        if bundle.metadata.status == Status.COMPLETE and not bundle.metadata.truncated:
            self.coverage.append(
                (
                    utc(bundle.metadata.effective_from).timestamp(),
                    utc(bundle.metadata.effective_to).timestamp(),
                )
            )

    def window(self, end, hours):
        lower = end.timestamp() - hours * 3600
        return [
            v for v in self.identities.values() if lower <= utc(v.published_at).timestamp() < end.timestamp()
        ]

    def covered(self, end, hours):
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

    def prune(self, end, hours=48):
        lower = end.timestamp() - hours * 3600
        self.identities = {
            k: v for k, v in self.identities.items() if utc(v.published_at).timestamp() >= lower
        }
        self.coverage = [(a, b) for a, b in self.coverage if b >= lower]


class TrendEngine:
    """Deterministic YouTube components following MathCore notebook v2 sections 4Р Р†Р вЂљРІР‚Сљ12.

    Query search has no incidence denominator. Components use a explicitly versioned
    publication-activity proxy. Production Trend Score stays null because Reddit,
    News and fixed-panel incidence do not exist here. The optional research score
    has explicit reduced weights and heuristic lifecycle thresholds.
    """

    def __init__(self, config: TrendConfig | None = None):
        self.config = config or TrendConfig()
        self.state = TrendState()
        self.enricher = TrendEnricher()
        self.like_history, self.comment_history = {}, {}
        self.pending_engagement = []

    def engagement(self, deltas: list[CounterDelta]) -> float | None:
        values = []
        for delta in deltas:
            if delta.like_rate is None or delta.comment_rate is None or delta.correction:
                continue
            ql = percentile(delta.like_rate, self.like_history.get(delta.age_bucket, []))
            qc = percentile(delta.comment_rate, self.comment_history.get(delta.age_bucket, []))
            if ql is not None and qc is not None:
                values.append(0.7 * ql + 0.3 * qc)
        # Normalize every member of this observation against the same prior cohort.
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
        self.enricher.ingest(bundle)
        config, state = self.config, self.state
        moment = utc(bundle.metadata.timestamp)
        # Completed, non-overlapping publication buckets keep poll cadence out of x.
        seconds = config.window_hours * 3600
        end = moment.fromtimestamp(math.floor(moment.timestamp() / seconds) * seconds, moment.tzinfo)
        timestamp = iso(end)
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
        # Conservative origin-capped effective support: each creator contributes <=1.
        n_eff = float(origins)
        valid = (
            bundle.metadata.status == Status.COMPLETE
            and not bundle.metadata.truncated
            and self.enricher.covered(end, config.window_hours)
        )
        # A capped page is a lower-bound sample; it must not update a full-window baseline.
        row = {
            "timestamp": bundle.metadata.finished_at,
            "window_end": timestamp,
            "topic": bundle.topic.canonical_name,
            "batch_id": bundle.metadata.batch_id,
            "formula_version": config.formula_version,
            "signal_basis": "youtube_query_publication_activity",
            "youtube_activity_count": len(items) if valid else None,
            "sampled_video_count": len(items),
            "youtube_activity_rate": len(items) / config.window_hours if valid else None,
            "unique_creators": creators,
            "effective_sample_size": n_eff,
            "ewma": None,
            "velocity": None,
            "acceleration": None,
            "growth": None,
            "burst": None,
            "engagement": None,
            "breadth": None,
            "confidence_N": min(n_eff / 50, 1),
            "confidence_D": 0.5 * min(origins / 10, 1) + 0.5 / 3,
            "freshness": 1.0
            if valid
            else (2 ** (-hours / config.half_life_hours) if state.ewma is not None else 0.0),
            "completeness": (state.valid_windows + int(valid)) / state.expected_windows,
            "confidence_O": None,
            "confidence": None,
            "source_cap": config.source_cap,
            "trend_score": None,
            "youtube_research_score": None,
            "lifecycle": state.lifecycle,
            "score_status": "UNAVAILABLE: production source panel absent",
            "research_gate": "INSUFFICIENT_EVIDENCE",
        }
        if not valid:
            row["research_gate"] = "INCOMPLETE_OR_CAPPED_COLLECTION"
            state.last_timestamp = timestamp
            state.velocity = None
            state.ewma = None
            state.contiguous_windows = 0
            self.pending_engagement.clear()
            return row
        rate = row["youtube_activity_rate"]
        smoothed, velocity, acceleration, growth, burst, breadth = self._components(
            rate, hours, previous_time, creators
        )
        engagement = mean(self.pending_engagement) if self.pending_engagement else None
        self.pending_engagement.clear()
        state.valid_windows += 1
        state.contiguous_windows = state.contiguous_windows + 1 if slots == 1 else 1
        state.valid_duration_hours += config.window_hours
        duration = min(state.valid_duration_hours / 3, 1)
        confidence = min(
            0.3 * row["confidence_N"]
            + 0.2 * row["confidence_D"]
            + 0.2 * row["freshness"]
            + 0.2 * row["completeness"]
            + 0.1 * duration,
            config.source_cap,
        )
        row.update(
            ewma=smoothed,
            velocity=velocity,
            acceleration=acceleration,
            growth=growth,
            burst=burst,
            engagement=engagement,
            breadth=breadth,
            confidence_O=duration,
            confidence=confidence,
        )
        self._publish(row, origins, n_eff)
        row["lifecycle"] = state.lifecycle
        state.history.append({"ewma": smoothed, "velocity": velocity, "creators": creators})
        state.history = state.history[-config.baseline_limit :]
        state.last_timestamp, state.ewma, state.velocity = timestamp, smoothed, velocity
        self.enricher.prune(end)
        return row

    def _components(self, rate: float, hours: float, previous_time, creators: int) -> tuple:
        """Compute derivatives and causal normalization against the prior state only."""
        config, state = self.config, self.state
        contiguous = (
            previous_time is not None
            and hours <= config.max_gap_windows * config.window_hours
            and state.velocity is not None
        )
        smoothed = (
            rate
            if state.ewma is None
            else rate * (1 - 2 ** (-hours / config.half_life_hours))
            + state.ewma * 2 ** (-hours / config.half_life_hours)
        )
        velocity = (
            (math.log1p(smoothed) - math.log1p(state.ewma)) / hours
            if previous_time
            and state.ewma is not None
            and hours <= config.max_gap_windows * config.window_hours
            else None
        )
        acceleration = (velocity - state.velocity) / hours if velocity is not None and contiguous else None
        prior = state.history
        enough = len(prior) >= config.baseline_min_windows
        positive = [r["velocity"] for r in prior if r["velocity"] is not None and r["velocity"] > 0]
        growth = (
            min(
                max(velocity or 0, 0) / max(quantile(positive, 0.9) if positive else 0, config.slope_floor), 1
            )
            if enough and velocity is not None
            else None
        )
        burst = None
        if enough:
            logs = [math.log1p(r["ewma"]) for r in prior]
            center = median(logs)
            scale = max(1.4826 * median(abs(v - center) for v in logs), 0.25)
            burst = min(max((math.log1p(smoothed) - center) / scale, 0) / 4, 1)
        breadth = percentile(creators, [r["creators"] for r in prior]) if enough else None
        return smoothed, velocity, acceleration, growth, burst, breadth

    def _publish(self, row: dict, origins: int, n_eff: float) -> None:
        """Apply explicit research gates and heuristic lifecycle; production T stays null."""
        config, state = self.config, self.state
        growth, burst, breadth = row["growth"], row["burst"], row["breadth"]
        confidence, engagement, velocity = row["confidence"], row["engagement"], row["velocity"]
        gate = (
            growth is not None
            and burst is not None
            and breadth is not None
            and n_eff >= config.min_support
            and origins >= config.min_creators
            and state.contiguous_windows >= 3
            and confidence >= 0.45
            and row["completeness"] >= 0.8
        )
        if gate:
            # Separate named formula versions avoid silently renormalizing production weights.
            score = 100 * (
                (0.45 * growth + 0.25 * burst + 0.15 * engagement + 0.10 * breadth) / 0.95
                if engagement is not None
                else (0.55 * growth + 0.30 * burst + 0.15 * breadth)
            )
            row["youtube_research_score"] = score
            row["research_gate"] = (
                "PASS_WITH_ENGAGEMENT" if engagement is not None else "PASS_NO_ENGAGEMENT_v1"
            )
            if score >= config.breakout_score and (velocity or 0) > 0:
                state.lifecycle = "BREAKOUT"
            elif state.lifecycle in ("RISING", "BREAKOUT") and (velocity or 0) <= 0:
                state.lifecycle = "PEAK"
            elif score >= config.rising_score and (velocity or 0) > 0:
                state.lifecycle = "RISING"
            elif (velocity or 0) < 0:
                state.lifecycle = "COOLING"
            else:
                state.lifecycle = "WATCHING"
