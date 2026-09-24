"""Mathematical calculation primitives for Gap metric computation and normalization."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import log
from statistics import median

from research.core.models import RawTopicVideo


@dataclass(frozen=True)
class MetricConfig:
    """Configuration parameters for Gap Metric Engine calculations.

    Attributes:
        half_life_hours: Half-life period in hours for exponential decay (default: 12.0).
        epsilon: Small positive float to prevent division by zero (default: 1e-6).
        demand_weight_er: Weight assigned to engagement rate in demand formula (default: 0.5).
        demand_weight_pr: Weight assigned to performance ratio in demand formula (default: 0.5).
        extreme_percent: Fraction of sorted tail elements used for boundary calculation (default: 0.10).
    """

    half_life_hours: float = 12.0
    epsilon: float = 1e-6
    demand_weight_er: float = 0.5
    demand_weight_pr: float = 0.5
    extreme_percent: float = 0.10

    def validate(self) -> None:
        """Validate parameter constraints.

        Raises:
            ValueError: If parameters violate numerical or probabilistic constraints.
        """
        if self.half_life_hours <= 0:
            raise ValueError("half_life_hours must be > 0")
        if self.epsilon <= 0:
            raise ValueError("epsilon must be > 0")
        if self.demand_weight_er < 0 or self.demand_weight_pr < 0:
            raise ValueError("Demand weights must be non-negative")
        if abs((self.demand_weight_er + self.demand_weight_pr) - 1.0) > 1e-9:
            raise ValueError("Demand weights must sum to 1")
        if not 0 < self.extreme_percent <= 0.5:
            raise ValueError("extreme_percent must be in (0, 0.5]")


def decay_factor(delta_hours: float, half_life_hours: float) -> float:
    """Calculate exponential half-life decay factor for a given elapsed time.

    Formula:
        decay = 2 ** (-delta_hours / half_life_hours)

    Mathematical representation:
        decay = 2^(-Δt / H)

    Args:
        delta_hours: Elapsed time in hours since the last measurement (Δt >= 0).
        half_life_hours: Half-life parameter in hours (H > 0).

    Returns:
        Decay multiplier in range (0.0, 1.0].
    """
    if delta_hours < 0:
        raise ValueError("delta_hours cannot be negative")
    return 2.0 ** (-delta_hours / half_life_hours)


def view_rate(views: int | float, age_hours: float, epsilon: float) -> float:
    """Calculate view accumulation velocity (views per hour).

    Formula:
        view_rate = views / (max(age_hours, 0.0) + epsilon)

    Mathematical representation:
        rate = views / (max(age, 0) + ε)

    Args:
        views: Total view count.
        age_hours: Video age in hours since publication.
        epsilon: Smoothing constant to avoid division by zero.

    Returns:
        Calculated view velocity (views per hour).
    """
    return float(views) / (max(age_hours, 0.0) + epsilon)


def percentile_extreme_median(values: Iterable[float], extreme_percent: float) -> tuple[float, float]:
    """Calculate robust lower and upper normalization boundaries using extreme tail medians.

    Selects the bottom and top `extreme_percent` portions of sorted values and returns
    the median of each extreme subset.

    Formula:
        ordered = sort(values)
        k = max(1, floor(len(ordered) * extreme_percent))
        low = median(ordered[0 : k])
        high = median(ordered[-k : len(ordered)])

    Mathematical representation:
        low = median({ v_(i) | 1 <= i <= k })
        high = median({ v_(N - k + i) | 1 <= i <= k })
        where k = max(1, ⌊N · p⌋)

    Args:
        values: Sequence of numeric observations.
        extreme_percent: Fraction of values in each tail (0 < p <= 0.5).

    Returns:
        Tuple of (low_boundary, high_boundary).
    """
    ordered = sorted(float(v) for v in values)
    if not ordered:
        raise ValueError("Cannot calculate normalization boundaries from empty values")

    count = max(1, int(len(ordered) * extreme_percent))
    low_values = ordered[:count]
    high_values = ordered[-count:]
    return median(low_values), median(high_values)


def log_min_max_normalize(
    value: float,
    low: float,
    high: float,
    epsilon: float,
) -> float:
    """Perform logarithmic min-max normalization mapped to [0.0, 1.0].

    Formula:
        numerator = ln(max(value, 0.0) + epsilon) - ln(max(low, 0.0) + epsilon)
        denominator = ln(max(high, 0.0) + epsilon) - ln(max(low, 0.0) + epsilon)
        normalized = numerator / denominator
        result = min(max(normalized, 0.0), 1.0)

    Mathematical representation:
        norm(v) = clip( [ln(v + ε) - ln(low + ε)] / [ln(high + ε) - ln(low + ε)], 0.0, 1.0 )

    Degenerate cases:
        If high <= low: returns 0.5 if |value - low| <= epsilon, else 1.0 if value > high else 0.0.

    Args:
        value: Input value to normalize.
        low: Lower normalization anchor boundary.
        high: Upper normalization anchor boundary.
        epsilon: Small positive constant for logarithmic stability.

    Returns:
        Normalized value clipped to [0.0, 1.0].
    """
    if high <= low:
        return 0.5 if abs(value - low) <= epsilon else (1.0 if value > high else 0.0)

    numerator = log(max(value, 0.0) + epsilon) - log(max(low, 0.0) + epsilon)
    denominator = log(max(high, 0.0) + epsilon) - log(max(low, 0.0) + epsilon)
    normalized = numerator / denominator
    return max(0.0, min(1.0, normalized))


def weighted_state_update(
    previous: float, current: float, delta_hours: float, half_life_hours: float
) -> float:
    """Update an exponential moving average (EMA) state given elapsed time and half-life.

    Formula:
        decay = 2 ** (-delta_hours / half_life_hours)
        updated = previous * decay + current * (1.0 - decay)

    Mathematical representation:
        S_t = S_(t-1) · 2^(-Δt / H) + X_t · (1 - 2^(-Δt / H))

    Args:
        previous: Previous historical state value S_(t-1).
        current: Newly observed batch value X_t.
        delta_hours: Time elapsed in hours Δt.
        half_life_hours: Half-life decay parameter H.

    Returns:
        Updated smoothed state S_t.
    """
    decay = decay_factor(delta_hours, half_life_hours)
    return previous * decay + current * (1.0 - decay)


def creator_median_views_rate(video: RawTopicVideo, epsilon: float) -> float:
    """Calculate the median view rate across recent baseline videos of a creator.

    Formula:
        rates = [stat.views / (max(stat.video_age_hours, 0.0) + epsilon) for stat in video.recent_video_stats]
        creator_rate = median(rates)

    Mathematical representation:
        rate_creator = median({ views_i / (age_i + ε) | i ∈ recent_videos })

    Args:
        video: Topic video containing recent historical baseline stats for its channel.
        epsilon: Smoothing constant.

    Returns:
        Median baseline view velocity for the creator.
    """
    rates = [view_rate(stat.views, stat.video_age_hours, epsilon) for stat in video.recent_video_stats]
    if not rates:
        raise ValueError(f"Video {video.video_id} has no recent video stats")
    return median(rates)
