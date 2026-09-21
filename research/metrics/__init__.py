"""Metrics package: Gap and Trend mathematical calculation engines, tracking, and evaluation."""

from research.metrics.calculators import (
    MetricConfig,
    creator_median_views_rate,
    decay_factor,
    log_min_max_normalize,
    percentile_extreme_median,
    view_rate,
    weighted_state_update,
)
from research.metrics.evaluation import (
    EventEvaluator,
    HistoricalAnalyzer,
)
from research.metrics.gap import (
    GapEngine,
    GapEnricher,
)
from research.metrics.processing import (
    MetricProcessor,
)
from research.metrics.tracking import (
    CounterDelta,
    TrackedVideoRegistry,
)
from research.metrics.trend import (
    TrendConfig,
    TrendEngine,
    TrendEnricher,
    TrendState,
    percentile,
    quantile,
)

__all__ = [
    "CounterDelta",
    "EventEvaluator",
    "GapEngine",
    "GapEnricher",
    "HistoricalAnalyzer",
    "MetricConfig",
    "MetricProcessor",
    "TrackedVideoRegistry",
    "TrendConfig",
    "TrendEngine",
    "TrendEnricher",
    "TrendState",
    "creator_median_views_rate",
    "decay_factor",
    "log_min_max_normalize",
    "percentile",
    "percentile_extreme_median",
    "quantile",
    "view_rate",
    "weighted_state_update",
]
