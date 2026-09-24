"""PySide6 graphical user interface desktop console for CreatorRadar Research."""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
from pathlib import Path
from threading import Event

import pyqtgraph as pg
from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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

from research.api.profiles import ApiProfiles
from research.core.domain import ExperimentConfig, encode
from research.core.time import utc
from research.gui.analytics import AnalyticsPanel
from research.gui.form import ExperimentForm
from research.orchestration.application import ExperimentManager
from research.storage.io import read_json, read_jsonl, write_json

log = logging.getLogger(__name__)


class ResearchWorker(QObject):
    """Executes long-running experiment and replay jobs in a dedicated worker thread."""

    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, operation) -> None:
        super().__init__()
        self.operation = operation

    @Slot()
    def run(self) -> None:
        """Run the wrapped operation and signal completion, progress, or failure."""
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
    """Main desktop application window for configuring experiments, running collections, and viewing analytics."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.manager = ExperimentManager(root, ApiProfiles(root / ".env"))
        self.thread: QThread | None = None
        self.worker: ResearchWorker | None = None
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
        self.analytics = AnalyticsPanel(root, db=self.manager.store)
        self.tabs.addTab(self.analytics, "Analytics & known events")
        self.quota_text, self.log_text = QPlainTextEdit(), QPlainTextEdit()
        self.quota_text.setReadOnly(True)
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(1000)
        self.tabs.addTab(self.quota_text, "API & quota")
        self.tabs.addTab(self.log_text, "Run log")
        self.estimate_button.clicked.connect(self.estimate_experiment)
        self.run_button.clicked.connect(self.run_experiment)
        self.stop_button.clicked.connect(self.stop_event.set)
        self.save_template.clicked.connect(self.save_config)
        self.load_template.clicked.connect(self.load_config)
        self.setStyleSheet(
            "QMainWindow,QWidget{font-family:Segoe UI;font-size:12px;} "
            "QPushButton{padding:7px 14px;} "
            "QTabWidget::pane{border:1px solid #bcc8d6;} "
            "QGroupBox{font-weight:600;}"
        )

    def run_experiment(self) -> None:
        """Launch an experiment job in the background worker thread."""
        try:
            config = self.form.config()
        except ValueError as exc:
            self.show_error(str(exc))
            return

        def operation(progress):
            return self.manager.run(config, self.stop_event, progress)

        self.start_worker(operation)

    def run_replay(self) -> None:
        """Launch an offline replay comparison job in the background worker thread."""
        topic_id = self.replay_source.currentText() if hasattr(self, "replay_source") else ""
        if not topic_id:
            self.show_error("Select a candidate topic for replay")
            return
        try:
            from research.orchestration.replay import ReplayService
            replay_service = ReplayService(self.manager.store)
        except Exception as exc:
            self.show_error(str(exc))
            return

        def operation(progress):
            return replay_service.replay_topic(topic_id, notify=progress)

        self.start_worker(operation)

    def estimate_experiment(self) -> None:
        """Estimate the experiment cost."""
        try:
            config = self.form.config()
        except ValueError as exc:
            self.show_error(str(exc))
            return

        def operation(progress):
            return self.manager.estimate(config)

        self.start_worker(operation)

    def start_worker(self, operation) -> None:
        """Initialize and start background worker thread."""
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
        self.stop_button.setEnabled(True)
        self.form.setEnabled(False)
        self.thread.start()

    @Slot(object)
    def on_progress(self, event) -> None:
        """Handle progress event emitted from worker thread."""
        self.log_text.appendPlainText(json.dumps(encode(event), ensure_ascii=False))
        self.status.setText(str(event.get("operation", "Working")) + " • " + str(event.get("status", "")))
        if "quota" in event:
            self.quota_text.setPlainText(json.dumps(event["quota"], indent=2))
        if event.get("status") == "WAITING_FOR_QUOTA":
            self.status.setText(
                "Waiting for quota reset • expected resume: " + str(event.get("expected_resume", "unknown"))
            )

    @Slot(object)
    def on_complete(self, result) -> None:
        """Handle worker successful completion."""
        if isinstance(result, str):
            self.analytics.add_experiment(result)
            self.status.setText("Saved: " + str(result))
        elif hasattr(result, "search_calls"):
            self.status.setText(
                f"Estimate: {result.search_calls} search calls ({result.search_units} units), "
                f"{result.other_units} other units. Status: {result.status}"
            )
            self.quota_text.setPlainText(
                f"Quota Estimate:\nStatus: {result.status}\nSearch calls: {result.search_calls}\n"
                f"Search units: {result.search_units}\nOther units: {result.other_units}"
            )
        elif isinstance(result, list):
            self.status.setText("Replay completed.")
        else:
            self.analytics.refresh()
            self.status.setText("Finished: " + str(result))

    @Slot()
    def worker_finished(self) -> None:
        """Reset UI controls after worker terminates."""
        self.thread = None
        self.worker = None
        self.run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.form.setEnabled(True)

    @Slot(str)
    def show_error(self, message: str) -> None:
        """Display an error message dialog and update status bar."""
        self.status.setText(message)
        QMessageBox.warning(self, "CreatorRadar", message)

    def save_config(self) -> None:
        """Export current form parameters to a reusable template JSON file."""
        try:
            config = self.form.config()
            path, _ = QFileDialog.getSaveFileName(
                self, "Save experiment template", str(self.root / "template.json"), "JSON (*.json)"
            )
            if path:
                write_json(Path(path), config)
        except (ValueError, TypeError, OSError) as exc:
            self.show_error(str(exc))

    def load_config(self) -> None:
        """Load experiment parameters from a template JSON file."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load experiment template", str(self.root), "JSON (*.json)"
        )
        if path:
            try:
                self.form.load_config(ExperimentConfig.from_dict(read_json(Path(path))))
            except (ValueError, TypeError, OSError) as exc:
                self.show_error(str(exc))

    def closeEvent(self, event) -> None:
        """Safely intercept window closure if an experiment is actively executing."""
        if self.thread is not None:
            self.stop_event.set()
            self.status.setText("Stopping safely. Close again after the current request finishes.")
            event.ignore()
        else:
            event.accept()


def main() -> None:
    """Launch the CreatorRadar Research Console GUI application."""
    parser = argparse.ArgumentParser(description="CreatorRadar Research Console")
    parser.add_argument("--debug", action="store_true", help="Enable debug level application logging")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent.parent
    (root / "data").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                root / "data" / "research.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            ),
            logging.StreamHandler(),
        ],
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    app = QApplication(sys.argv[:1])
    window = ResearchConsole(root)
    window.show()
    sys.exit(app.exec())
