"""State persistence and normalization boundaries for Gap Metric Engine."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class NormalizationBoundaryState:
    """Persistent low/high EMA boundaries for normalizing a specific metric.

    Attributes:
        low: Smoothed lower normalization boundary.
        high: Smoothed upper normalization boundary.
    """

    low: float = -1.0
    high: float = -1.0

    def to_dict(self) -> dict[str, float]:
        """Serialize boundaries to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> NormalizationBoundaryState:
        """Deserialize boundaries from dictionary."""
        return cls(low=float(value.get("low", -1.0)), high=float(value.get("high", -1.0)))


@dataclass
class MetricsState:
    """Historical state of the Gap Metric Engine across successive batch updates.

    Attributes:
        initialized: True if at least one batch has initialized this state.
        last_update_time: ISO-8601 timestamp of last processed batch.
        topic_rate: Smoothed view rate of the topic.
        supply: Smoothed creator supply metric.
        demand: Smoothed creator demand metric.
        normalization: Dictionary mapping metric keys ('er', 'pr', 'supply') to boundary states.
    """

    initialized: bool = False
    last_update_time: str | None = None
    topic_rate: float = -1.0
    supply: float = -1.0
    demand: float = -1.0
    normalization: dict[str, NormalizationBoundaryState] = field(
        default_factory=lambda: {
            "er": NormalizationBoundaryState(),
            "pr": NormalizationBoundaryState(),
            "supply": NormalizationBoundaryState(),
        }
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize state to dictionary."""
        return {
            "initialized": self.initialized,
            "last_update_time": self.last_update_time,
            "topic_rate": self.topic_rate,
            "supply": self.supply,
            "demand": self.demand,
            "normalization": {key: value.to_dict() for key, value in self.normalization.items()},
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MetricsState:
        """Deserialize state from dictionary."""
        raw_norm = value.get("normalization", {})
        state = cls(
            initialized=bool(value.get("initialized", False)),
            last_update_time=value.get("last_update_time"),
            topic_rate=float(value.get("topic_rate", -1.0)),
            supply=float(value.get("supply", -1.0)),
            demand=float(value.get("demand", -1.0)),
        )
        for key in ("er", "pr", "supply"):
            if key in raw_norm:
                state.normalization[key] = NormalizationBoundaryState.from_dict(raw_norm[key])
        return state


class StateManager:
    """File manager for loading and saving MetricsState JSON documents."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> MetricsState:
        """Load state from disk or return a fresh uninitialized state."""
        if not self.path.exists():
            return MetricsState()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return MetricsState.from_dict(payload)

    def save(self, state: MetricsState) -> None:
        """Persist state to disk in formatted JSON."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
