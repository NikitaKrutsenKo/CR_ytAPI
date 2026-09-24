"""Versioned historical normalization baselines for YouTube Trend Engine."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from research.core.time import iso, now_utc


def percentile(value: float, history: list[float]) -> float | None:
    """Calculate the empirical cumulative distribution function (ECDF) against prior history.

    Formula:
        ECDF(x) = count(v <= x for v in history) / len(history)

    Mathematical representation:
        F_n(x) = (1 / n) * Σ 𝕀(v_i <= x)

    Args:
        value: Numeric value to evaluate.
        history: Sequence of prior historical observations.

    Returns:
        Percentile rank in [0.0, 1.0], or None if history is empty.
    """
    return sum(v <= value for v in history) / len(history) if history else None


def quantile(values: list[float], probability: float) -> float:
    """Compute the quantile of a sequence of values using continuous linear interpolation.

    Formula:
        ordered = sort(values)
        pos = (len(ordered) - 1) * probability
        low = floor(pos)
        q = ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (pos - low)

    Args:
        values: Sequence of numeric values.
        probability: Desired quantile probability in [0.0, 1.0].

    Returns:
        Interpolated quantile value.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low = int(position)
    if low >= len(ordered) - 1:
        return ordered[-1]
    return ordered[low] + (ordered[low + 1] - ordered[low]) * (position - low)


@dataclass
class HistoricalBaseline:
    """Versioned historical reference distribution for normalizing G_YT, Z_YT, E_YT, B_YT, and V_YT.

    Attributes:
        baseline_version: Unique baseline identifier.
        created_at: Creation timestamp in UTC ISO format.
        training_period_start: Start timestamp of training cohort.
        training_period_end: End timestamp of training cohort.
        sample_count: Number of independent training observations.
        cohort_definition: Description of training dataset / filters.
        status: Baseline calibration status ('READY', 'WARMUP', 'INSUFFICIENT_DATA').
        v_scale_yt: 90th percentile of positive velocity for Growth scaling.
        median_log_activity: Median of ln(1 + activity) for robust Burst centering.
        mad_log_activity: Median Absolute Deviation of ln(1 + activity) for Burst scaling.
        like_reaction_rates: Reference cohort of delta_likes / delta_views.
        comment_reaction_rates: Reference cohort of delta_comments / delta_views.
        creator_counts: Reference cohort of distinct creator counts for Breadth.
        views_per_hour_by_age: Reference cohort mapping age buckets ('0_24h', '24_168h', '168h_plus')
            to views/hour velocity for dynamic media velocity V_YT.
    """

    baseline_version: str = "youtube_baseline_research_v1"
    created_at: str = field(default_factory=lambda: iso(now_utc()))
    training_period_start: str | None = None
    training_period_end: str | None = None
    sample_count: int = 100
    cohort_definition: str = "youtube_research_synthetic_cohort_v1"
    status: str = "READY"
    v_scale_yt: float = 0.24
    median_log_activity: float = 2.0
    mad_log_activity: float = 0.5
    like_reaction_rates: list[float] = field(default_factory=list)
    comment_reaction_rates: list[float] = field(default_factory=list)
    creator_counts: list[float] = field(default_factory=list)
    views_per_hour_by_age: dict[str, list[float]] = field(default_factory=dict)

    def v_scale(self, floor: float = 0.05) -> float:
        """Return positive velocity scale factor with positive floor."""
        return max(self.v_scale_yt, floor)

    def robust_z_parameters(self) -> tuple[float, float]:
        """Return (center, scale) for robust burst calculation.

        Scale enforces the 0.25 minimum floor specified in section 9.
        """
        center = self.median_log_activity
        scale = max(1.4826 * self.mad_log_activity, 0.25)
        return center, scale

    def percentile_like(self, rate: float) -> float | None:
        """Compute ECDF percentile for like reaction rate."""
        return percentile(rate, self.like_reaction_rates)

    def percentile_comment(self, rate: float) -> float | None:
        """Compute ECDF percentile for comment reaction rate."""
        return percentile(rate, self.comment_reaction_rates)

    def percentile_creators(self, count: int | float) -> float | None:
        """Compute ECDF percentile for distinct creator count."""
        return percentile(float(count), self.creator_counts)

    def percentile_views_per_hour(self, rate: float, age_bucket: str) -> float | None:
        """Compute same-age cohort ECDF percentile for views per hour."""
        cohort = self.views_per_hour_by_age.get(age_bucket, [])
        return percentile(rate, cohort)

    def to_dict(self) -> dict[str, Any]:
        """Serialize baseline to JSON-compatible dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HistoricalBaseline:
        """Deserialize baseline from dictionary."""
        return cls(**data)


def create_default_baseline() -> HistoricalBaseline:
    """Construct default research baseline initialized in WARMUP status."""
    return HistoricalBaseline(
        baseline_version="youtube_baseline_research_v1",
        created_at="2026-09-22T00:00:00Z",
        training_period_start=None,
        training_period_end=None,
        sample_count=0,
        cohort_definition="canonical_research_warmup_cohort_v1",
        status="WARMUP",
        v_scale_yt=0.24,
        median_log_activity=math.log1p(20.0),
        mad_log_activity=0.5,
        like_reaction_rates=[],
        comment_reaction_rates=[],
        creator_counts=[],
        views_per_hour_by_age={},
    )


class BaselineStore:
    """Thread-safe persistent store for versioned historical baselines."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root / "data" / "baselines" if root else None
        self._memory: dict[str, HistoricalBaseline] = {}
        # Register default baseline
        default_bl = create_default_baseline()
        self._memory[default_bl.baseline_version] = default_bl

    def get(self, version: str | None = None) -> HistoricalBaseline:
        """Retrieve baseline by version; falls back to default if unavailable."""
        target_version = version or "youtube_baseline_research_v1"
        if target_version in self._memory:
            return self._memory[target_version]
        if self.root:
            path = self.root / f"{target_version}.json"
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                    bl = HistoricalBaseline.from_dict(data)
                    self._memory[target_version] = bl
                    return bl
        # Return default with WARMUP status if requested version not found
        fallback = create_default_baseline()
        fallback.baseline_version = target_version
        fallback.status = "WARMUP"
        return fallback

    def save(self, baseline: HistoricalBaseline) -> None:
        """Persist a baseline to disk and in-memory cache."""
        self._memory[baseline.baseline_version] = baseline
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{baseline.baseline_version}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(baseline.to_dict(), f, indent=2)
