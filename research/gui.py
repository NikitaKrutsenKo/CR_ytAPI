from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Event

import pyqtgraph as pg
from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from research.application import ExperimentManager
from research.domain import ExperimentConfig, encode, utc
from research.gui_analytics import AnalyticsPanel
from research.gui_form import ExperimentForm
from research.profiles import ApiProfiles
from research.replay import ReplayService
from research.storage import read_json, read_jsonl, write_json

log = logging.getLogger(__name__)


class ResearchWorker(QObject):
    """Run application services outside Qt's UI thread; cancellation is cooperative."""

    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    @Slot()
    def run(self):
        try:
            result = self.operation(self.progress.emit)
            self.completed.emit(result)
        except Exception as exc:
            log.exception("background_operation_failed")
            message = (
                str(exc)
                if isinstance(exc, (ValueError, RuntimeError))
                else "Operation failed. See data/research.log for technical details."
            )
            self.failed.emit(message)
        finally:
            self.finished.emit()


class ResearchConsole(QMainWindow):
    """Thin desktop controller: configuration, service commands and persisted analytics."""

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.manager = ExperimentManager(root, ApiProfiles(root / ".env"))
        self.thread = None
        self.stop_event = Event()
        self.setWindowTitle("CreatorRadar • Research Console")
        self.resize(1380, 960)
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        experiment = QWidget()
        layout = QVBoxLayout(experiment)
        self.form = ExperimentForm(root, self.manager.profiles)
        layout.addWidget(self.form)
        buttons = QHBoxLayout()
        self.estimate_button, self.run_button, self.stop_button = (
            QPushButton("Estimate cost"),
            QPushButton("Run experiment"),
            QPushButton("Stop"),
        )
        self.stop_button.setEnabled(False)
        self.save_template, self.load_template = QPushButton("Save template"), QPushButton("Load template")
        for button in (
            self.estimate_button,
            self.run_button,
            self.stop_button,
            self.save_template,
            self.load_template,
        ):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.status = QLabel("Ready. Select an API profile and estimate the experiment cost.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.tabs.addTab(experiment, "Experiments & topics")
        self.analytics = AnalyticsPanel(root)
        self.tabs.addTab(self.analytics, "Analytics & known events")
        self.setup_replay()
        self.quota_text, self.log_text = QPlainTextEdit(), QPlainTextEdit()
        self.quota_text.setReadOnly(True)
        self.log_text.setReadOnly(True)
        self.tabs.addTab(self.quota_text, "API & quota")
        self.tabs.addTab(self.log_text, "Run log")
        self.estimate_button.clicked.connect(self.estimate)
        self.run_button.clicked.connect(self.run_experiment)
        self.stop_button.clicked.connect(self.stop_event.set)
        self.save_template.clicked.connect(self.save_config)
        self.load_template.clicked.connect(self.load_config)
        self.setStyleSheet(
            "QMainWindow,QWidget{font-family:Segoe UI;font-size:12px;} QPushButton{padding:7px 14px;} QTabWidget::pane{border:1px solid #bcc8d6;} QGroupBox{font-weight:600;}"
        )

    def setup_replay(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(
            QLabel(
                "Replay stored raw observations against one or more parameter sets. No API key or network is used."
            )
        )
        self.replay_source = QComboBox()
        refresh = QPushButton("Refresh experiments")
        self.refresh_replay = lambda: self.populate_replay()
        refresh.clicked.connect(self.populate_replay)
        layout.addWidget(self.replay_source)
        layout.addWidget(refresh)
        self.formulas = QListWidget()
        self.formulas.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for path in sorted((self.root / "config" / "formulas").glob("trend_*.json")):
            self.formulas.addItem(str(path))
        if self.formulas.count():
            self.formulas.item(0).setSelected(True)
        layout.addWidget(
            QLabel("Select Trend formulas (Ctrl-click to compare). Gap formula comes from Settings.")
        )
        layout.addWidget(self.formulas)
        self.replay_button = QPushButton("Replay / compare selected formulas")
        self.replay_button.clicked.connect(self.run_replay)
        layout.addWidget(self.replay_button)
        self.comparison = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(utcOffset=0)})
        self.comparison.addLegend()
        self.comparison.setLabel("left", "YouTube activity EWMA")
        layout.addWidget(self.comparison, 1)
        layout.addWidget(
            QLabel(
                "Comparison chart: first topic, activity EWMA. Full metrics and research scores are available in Analytics for each resulting experiment."
            )
        )
        self.tabs.addTab(page, "Replay & comparison")
        self.populate_replay()

    def populate_replay(self):
        self.replay_source.clear()
        self.replay_source.addItems(
            sorted(
                (p.name for p in (self.root / "experiments").glob("exp_*") if (p / "inputs.jsonl").exists()),
                reverse=True,
            )
        )

    def estimate(self):
        try:
            config = self.form.config()
            estimate = self.manager.estimate(config)
            usage = self.manager.quota(config).usage()
            self.quota_text.setPlainText(
                json.dumps({"plan": encode(estimate), "daily_usage": usage}, indent=2)
            )
            if estimate.endless:
                self.status.setText(
                    f"{estimate.status} • Endless estimate: {estimate.search_calls_per_hour:g} search calls/hour, "
                    f"{estimate.other_units_per_hour:g} other units/hour; collection pauses at configured quota "
                    "and resumes after the Pacific reset."
                )
            else:
                self.status.setText(
                    f"{estimate.status} • Local estimate: {estimate.search_calls} search calls; "
                    f"{estimate.other_units} other units; includes configured retry allowance."
                )
            return config
        except (ValueError, TypeError, OSError) as exc:
            self.show_error(str(exc))
            return None

    def run_experiment(self):
        config = self.estimate()
        if config:
            self.stop_event.clear()
            self.start_worker(lambda progress: self.manager.run(config, self.stop_event, progress))

    def run_replay(self):
        if not self.replay_source.currentText():
            self.show_error("Select an existing raw experiment")
            return
        source = self.root / "experiments" / self.replay_source.currentText()
        try:
            formulas = [read_json(Path(item.text())) for item in self.formulas.selectedItems()]
            gap = read_json(self.root / self.form.gap_formula.text())
            if not formulas:
                raise ValueError("Select at least one formula")
        except (ValueError, TypeError, OSError) as exc:
            self.show_error(str(exc))
            return

        def operation(progress):
            return [ReplayService(self.root).run(source, gap, formula, progress) for formula in formulas]

        self.start_worker(operation)

    def start_worker(self, operation):
        if self.thread is not None:
            return
        self.thread = QThread(self)
        self.worker = ResearchWorker(operation)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.completed.connect(self.on_complete)
        self.worker.failed.connect(self.show_error)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.worker_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.run_button.setEnabled(False)
        self.replay_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.form.setEnabled(False)
        self.thread.start()

    @Slot(object)
    def on_progress(self, event):
        self.log_text.appendPlainText(json.dumps(encode(event), ensure_ascii=False))
        self.status.setText(str(event.get("operation", "Working")) + " • " + str(event.get("status", "")))
        if "quota" in event:
            self.quota_text.setPlainText(json.dumps(event["quota"], indent=2))
        if event.get("status") == "WAITING_FOR_QUOTA":
            self.status.setText(
                "Waiting for quota reset • expected resume: " + str(event.get("expected_resume", "unknown"))
            )

    @Slot(object)
    def on_complete(self, result):
        self.analytics.refresh()
        self.populate_replay()
        self.status.setText("Saved locally: " + str(result))
        if isinstance(result, list):
            self.comparison.clear()
            self.comparison.plotItem.legend.clear()
            for index, path in enumerate(result):
                trend_files = sorted((path / "results").glob("*/trend.jsonl"))
                if trend_files:
                    rows = [r for r in read_jsonl(trend_files[0]) if r.get("ewma") is not None]
                    self.comparison.plot(
                        [utc(r["timestamp"]).timestamp() for r in rows],
                        [r["ewma"] for r in rows],
                        pen=pg.mkPen(pg.intColor(index), width=2),
                        symbol="o",
                        name=path.name,
                    )
        else:
            self.analytics.experiment.setCurrentText(result.name)

    @Slot()
    def worker_finished(self):
        self.thread = None
        self.run_button.setEnabled(True)
        self.replay_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.form.setEnabled(True)

    @Slot(str)
    def show_error(self, message):
        self.status.setText(message)
        QMessageBox.warning(self, "CreatorRadar", message)

    def save_config(self):
        try:
            config = self.form.config()
            path, _ = QFileDialog.getSaveFileName(
                self, "Save experiment template", str(self.root / "template.json"), "JSON (*.json)"
            )
            if path:
                write_json(Path(path), config)
        except (ValueError, TypeError, OSError) as exc:
            self.show_error(str(exc))

    def load_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load experiment template", str(self.root), "JSON (*.json)"
        )
        if path:
            try:
                self.form.load_config(ExperimentConfig.from_dict(read_json(Path(path))))
            except (ValueError, TypeError, OSError) as exc:
                self.show_error(str(exc))

    def closeEvent(self, event):
        if self.thread is not None:
            self.stop_event.set()
            self.status.setText("Stopping safely. Close again after the current request finishes.")
            event.ignore()
        else:
            event.accept()
