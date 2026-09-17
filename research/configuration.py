from dataclasses import asdict, replace

from metrics.calculators import MetricConfig
from research.domain import ExperimentConfig
from research.trend import TrendConfig


def resolve_formulas(config: ExperimentConfig) -> ExperimentConfig:
    """Freeze every default into run metadata so later code defaults cannot change replay."""
    gap = MetricConfig(**config.gap_formula)
    gap.validate()
    trend = TrendConfig(**config.trend_formula)
    return replace(config, gap_formula=asdict(gap), trend_formula=asdict(trend))
