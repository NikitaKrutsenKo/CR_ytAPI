from __future__ import annotations

from dataclasses import dataclass
from math import log
from statistics import mean, median
from typing import Iterable

from models.batch import YouTubeBatch
from models.raw_video import RawTopicVideo


@dataclass(frozen=True)
class MetricConfig:
    half_life_hours: float = 12.0
    epsilon: float = 1e-6
    demand_weight_er: float = 0.5
    demand_weight_pr: float = 0.5
    extreme_percent: float = 0.10

    def validate(self) -> None:
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
    if delta_hours < 0:
        raise ValueError("delta_hours cannot be negative")
    return 2.0 ** (-delta_hours / half_life_hours)


def view_rate(views: int | float, age_hours: float, epsilon: float) -> float:
    return float(views) / (max(age_hours, 0.0) + epsilon)


def percentile_extreme_median(values: Iterable[float], extreme_percent: float) -> tuple[float, float]:
    """Return robust low/high boundaries from bottom/top extreme portions.

    For small batches, at least one element is used for each side. The selected
    elements are sorted by value, then the median of the selected tail is used.
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
    if high <= low:
        return 0.5 if abs(value - low) <= epsilon else (1.0 if value > high else 0.0)

    numerator = log(max(value, 0.0) + epsilon) - log(max(low, 0.0) + epsilon)
    denominator = log(max(high, 0.0) + epsilon) - log(max(low, 0.0) + epsilon)
    normalized = numerator / denominator
    return max(0.0, min(1.0, normalized))


def weighted_state_update(previous: float, current: float, delta_hours: float, half_life_hours: float) -> float:
    decay = decay_factor(delta_hours, half_life_hours)
    return previous * decay + current * (1.0 - decay)


def creator_median_views_rate(video: RawTopicVideo, epsilon: float) -> float:
    rates = [
        view_rate(stat.views, stat.video_age_hours, epsilon)
        for stat in video.recent_video_stats
    ]
    if not rates:
        raise ValueError(f"Video {video.video_id} has no recent video stats")
    return median(rates)
