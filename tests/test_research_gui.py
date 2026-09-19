"""Headless Qt integration: UI config -> worker -> fake backend -> saved analytics."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import shutil
from pathlib import Path
from time import monotonic

import pytest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from research.application import ExperimentManager
from research.gui import ResearchConsole
from research.profiles import ApiProfiles
from tests.test_research_flows import RunClient


@pytest.fixture(scope="module")
def app():
    app = QApplication.instance() or QApplication([])
    font = Path("C:/Windows/Fonts/segoeui.ttf")
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    return app


def test_gui_run_config_worker_and_analytics(tmp_path, app):
    shutil.copytree(Path(__file__).parents[1] / "config" / "formulas", tmp_path / "config" / "formulas")
    window = ResearchConsole(tmp_path)
    profiles = ApiProfiles(tmp_path / "absent", {"YOUTUBE_API_KEY_DEFAULT": "fake"})
    window.manager = ExperimentManager(tmp_path, profiles, RunClient)
    window.form.duration.setValue(0.01 / 60)
    window.form.mode.setCurrentText("Combined Research")
    config = window.form.config()
    assert not hasattr(window.form, "hours")
    assert config.duration_hours == pytest.approx(0.01 / 60, abs=0.0001)
    assert config.minimum_video_age_hours == 1
    assert config.minimum_views == 1000
    assert "Derived width:" in window.form.preview.text()
    assert config.requests[0].requested_from.endswith("Z")
    errors = []
    window.show_error = errors.append
    window.run_experiment()
    deadline = monotonic() + 5
    while window.thread is not None and monotonic() < deadline:
        app.processEvents()
    assert window.thread is None
    assert not errors
    assert window.analytics.experiment.count() == 1
    assert window.analytics.topic.count() == 1
    window.tabs.setCurrentIndex(1)
    window.analytics.metric.setCurrentText("gap: gap_score")
    window.analytics.draw()
    assert window.analytics.plot.listDataItems()
    window.form.load_config(config)
    restored = window.form.config()
    assert restored.mode == config.mode
    assert restored.gap_formula == config.gap_formula
    window.close()
