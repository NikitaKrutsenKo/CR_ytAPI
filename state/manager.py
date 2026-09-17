from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class NormalizationBoundaryState:
    low: float = -1.0
    high: float = -1.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "NormalizationBoundaryState":
        return cls(low=float(value.get("low", -1.0)), high=float(value.get("high", -1.0)))


@dataclass
class MetricsState:
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
        return {
            "initialized": self.initialized,
            "last_update_time": self.last_update_time,
            "topic_rate": self.topic_rate,
            "supply": self.supply,
            "demand": self.demand,
            "normalization": {
                key: value.to_dict() for key, value in self.normalization.items()
            },
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MetricsState":
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
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> MetricsState:
        if not self.path.exists():
            return MetricsState()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return MetricsState.from_dict(payload)

    def save(self, state: MetricsState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
