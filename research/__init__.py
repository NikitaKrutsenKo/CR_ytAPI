"""CreatorRadar Research Package.

Subpackages:
    core: Domain entities, data models, time helpers, and configuration.
    api: YouTube API client, endpoint services, discovery, and quotas.
    metrics: Gap and Trend mathematical calculation engines, tracking, and evaluation.
    orchestration: Experiment run execution, scheduling, replay, and reporting.
    storage: File workspace management, JSON/JSONL I/O, and state persistence.
    gui: PySide6 desktop research console, forms, and analytics charts.
"""

from research import (
    api,
    core,
    gui,
    metrics,
    orchestration,
    storage,
)

__all__ = [
    "api",
    "core",
    "gui",
    "metrics",
    "orchestration",
    "storage",
]
