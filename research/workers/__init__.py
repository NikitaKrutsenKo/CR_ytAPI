"""Workers package: autonomous, stateless collection daemons and schedulers."""

from research.workers.collector import (
    CADENCE_MAP,
    CadenceSchedule,
    IngestionWorker,
    resolve_effective_cadence,
)

__all__ = [
    "CADENCE_MAP",
    "CadenceSchedule",
    "IngestionWorker",
    "resolve_effective_cadence",
]
