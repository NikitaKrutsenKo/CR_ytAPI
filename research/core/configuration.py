"""Configuration utilities and formula parameter resolution."""

from __future__ import annotations

from dataclasses import asdict, replace

from research.core.domain import ExperimentConfig
from research.metrics.calculators import MetricConfig
from research.metrics.trend import TrendConfig


def resolve_formulas(config: ExperimentConfig) -> ExperimentConfig:
    """Freeze every formula default into run metadata so later code defaults cannot change replay.

    Args:
        config: Original ExperimentConfig.

    Returns:
        ExperimentConfig with fully resolved gap_formula and trend_formula parameter dictionaries.
    """
    gap = MetricConfig(**config.gap_formula)
    gap.validate()
    trend = TrendConfig(**config.trend_formula)
    return replace(config, gap_formula=asdict(gap), trend_formula=asdict(trend))
