"""GUI presentation package: desktop console, experiment forms, and analytics charts."""

from research.gui.analytics import AnalyticsPanel
from research.gui.console import (
    ResearchConsole,
    ResearchWorker,
    main,
)
from research.gui.form import ExperimentForm

__all__ = [
    "AnalyticsPanel",
    "ExperimentForm",
    "ResearchConsole",
    "ResearchWorker",
    "main",
]
