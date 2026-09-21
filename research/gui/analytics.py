"""Analytics visualization panel: PyQtGraph timeseries, event overlays, and data export."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from research.core.domain import KnownEvent
from research.core.time import iso, now_utc, utc
from research.metrics.evaluation import EventEvaluator
from research.storage.io import append_jsonl, read_json, read_jsonl, write_json


class AnalyticsPanel(QWidget):
    """Interactive plotting panel for metric timeseries, known event alignment, and export."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root, self.rows, self.events = root, {}, []
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.experiment, self.topic, self.metric, self.second = (
            QComboBox(),
            QComboBox(),
            QComboBox(),
            QComboBox(),
        )
        self.refresh_button = QPushButton("Refresh")
        for label, widget in (
            ("Experiment", self.experiment),
            ("Topic", self.topic),
            ("Metric", self.metric),
            ("Overlay", self.second),
        ):
            toolbar.addWidget(QLabel(label))
            toolbar.addWidget(widget)
        toolbar.addWidget(self.refresh_button)
        layout.addLayout(toolbar)
        events_bar = QHBoxLayout()
        self.event = QComboBox()
        self.relative, self.show_events = (
            QCheckBox("Hours relative to event"),
            QCheckBox("Show event markers"),
        )
        self.show_events.setChecked(True)
        self.import_events, self.add_event = (
            QPushButton("Import events JSON"),
            QPushButton("Add verified event"),
        )
        self.export = QPushButton("Export selected series")
        for widget in (
            self.event,
            self.relative,
            self.show_events,
            self.import_events,
            self.add_event,
            self.export,
        ):
            events_bar.addWidget(widget)
        layout.addLayout(events_bar)
        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(utcOffset=0)})
        self.plot.setBackground("#111c2d")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        layout.addWidget(self.plot, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(180)
        layout.addWidget(self.details)
        self.refresh_button.clicked.connect(self.refresh)
        self.experiment.currentTextChanged.connect(self.load_topics)
        self.topic.currentTextChanged.connect(self.load_rows)
        for widget in (self.metric, self.second, self.event):
            widget.currentTextChanged.connect(self.draw)
        self.relative.toggled.connect(self.draw)
        self.show_events.toggled.connect(self.draw)
        self.import_events.clicked.connect(self.import_known_events)
        self.add_event.clicked.connect(self.add_known_event)
        self.export.clicked.connect(self.export_series)
        self.refresh()

    def error(self, exc: Exception) -> None:
        """Display error alert modal."""
        QMessageBox.warning(self, "Analytics", str(exc))

    def refresh(self) -> None:
        """Scan experiments folder and reload available experiment selection dropdown."""
        selected = self.experiment.currentText()
        self.experiment.blockSignals(True)
        self.experiment.clear()
        path = self.root / "experiments"
        self.experiment.addItems(sorted((p.name for p in path.glob("exp_*") if p.is_dir()), reverse=True))
        if selected:
            self.experiment.setCurrentText(selected)
        self.experiment.blockSignals(False)
        self.load_topics()

    def path(self) -> Path:
        """Return the directory path for the currently selected experiment."""
        return self.root / "experiments" / self.experiment.currentText()

    def load_topics(self) -> None:
        """Load topic folders available within the selected experiment."""
        self.topic.blockSignals(True)
        self.topic.clear()
        for path in sorted((self.path() / "results").glob("*")):
            if path.is_dir():
                self.topic.addItem(path.name)
        self.topic.blockSignals(False)
        self.load_rows()

    def load_rows(self) -> None:
        """Load result datasets and available metrics for the selected experiment and topic."""
        self.rows = {}
        try:
            for path in (self.path() / "results" / self.topic.currentText()).glob("*.jsonl"):
                self.rows[path.stem] = read_jsonl(path)
            for widget in (self.metric, self.second):
                widget.blockSignals(True)
                widget.clear()
            self.second.addItem("None")
            for kind, rows in self.rows.items():
                keys = sorted(
                    {
                        key
                        for row in rows
                        for key, value in row.items()
                        if isinstance(value, (float, int)) and not isinstance(value, bool)
                    }
                )
                for key in keys:
                    self.metric.addItem(f"{kind}: {key}")
                    self.second.addItem(f"{kind}: {key}")
            for widget in (self.metric, self.second):
                widget.blockSignals(False)
            for preferred in (
                "trend: youtube_research_score",
                "trend: youtube_activity_count",
                "gap: gap_score",
                "historical: sampled_publication_count",
            ):
                if self.metric.findText(preferred) >= 0:
                    self.metric.setCurrentText(preferred)
                    break
            path = self.path() / "known_events.jsonl"
            self.events = [KnownEvent(**v) for v in read_jsonl(path)] if path.exists() else []
            self.event.blockSignals(True)
            self.event.clear()
            for event in self.events:
                if event.topic_id == self.topic.currentText():
                    self.event.addItem(event.event_name, event)
            self.event.blockSignals(False)
            self.draw()
        except (ValueError, OSError) as exc:
            self.error(exc)

    def draw(self) -> None:
        """Plot selected metric series and render ground-truth event vertical lines."""
        self.plot.clear()
        if self.plot.plotItem.legend:
            self.plot.plotItem.legend.clear()
        else:
            self.plot.addLegend()
        event = self.event.currentData()
        relative = self.relative.isChecked() and event is not None
        axis = pg.AxisItem("bottom") if relative else pg.DateAxisItem(utcOffset=0)
        self.plot.setAxisItems({"bottom": axis})
        self.plot.setLabel("bottom", "Hours relative to known event" if relative else "UTC calendar time")
        for selection, color in (
            (self.metric.currentText(), "#45d6ba"),
            (self.second.currentText(), "#ffbd69"),
        ):
            if ": " not in selection:
                continue
            kind, field = selection.split(": ", 1)
            points = [(utc(r["timestamp"]).timestamp(), r.get(field)) for r in self.rows.get(kind, [])]
            valid = [(t, v if isinstance(v, (int, float)) else math.nan) for t, v in points]
            if valid:
                x = [(t - utc(event.event_time).timestamp()) / 3600 if relative else t for t, _ in valid]
                self.plot.plot(
                    x,
                    [v for _, v in valid],
                    pen=pg.mkPen(color, width=2),
                    symbol="o",
                    symbolSize=5,
                    name=selection,
                    connect="finite",
                )
        if self.show_events.isChecked():
            for index in range(self.event.count()):
                known = self.event.itemData(index)
                x = utc(known.event_time).timestamp()
                if relative:
                    x = (x - utc(event.event_time).timestamp()) / 3600
                self.plot.addItem(
                    pg.InfiniteLine(
                        x,
                        angle=90,
                        pen=pg.mkPen("#f6a0c4", style=Qt.PenStyle.DashLine),
                        label=known.event_name,
                    )
                )
        details = {kind: rows[-1] for kind, rows in self.rows.items() if rows}
        if event:
            details["event_evaluation"] = EventEvaluator().evaluate(self.rows.get("trend", []), event)
        self.details.setPlainText(json.dumps(details, ensure_ascii=False, indent=2))

    def import_known_events(self) -> None:
        """Prompt user for a JSON file of KnownEvent items and import them into the experiment."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Verified KnownEvent array", str(self.root), "JSON (*.json)"
        )
        if not path:
            return
        try:
            events = [KnownEvent(**v) for v in read_json(Path(path))]
            allowed = {p["topic_id"] for p in read_json(self.path() / "topics.json")}
            if any(e.topic_id not in allowed for e in events):
                raise ValueError("Event topic_id is not in this experiment")
            for event in events:
                append_jsonl(self.path() / "known_events.jsonl", event)
            self.load_rows()
        except (ValueError, TypeError, KeyError) as exc:
            self.error(exc)

    def add_known_event(self) -> None:
        """Open dialog to manually record and verify a KnownEvent entry."""
        if not self.topic.currentText():
            return
        template = {
            "topic_id": self.topic.currentText(),
            "event_name": "",
            "event_type": "announcement",
            "event_time": "",
            "description": "",
            "source_url": "",
            "source_type": "official",
            "verified_at": iso(now_utc()),
        }
        text, accepted = QInputDialog.getMultiLineText(
            self,
            "Verified event",
            "Enter verified UTC timestamp and traceable source:",
            json.dumps(template, indent=2),
        )
        if not accepted:
            return
        try:
            event = KnownEvent(**json.loads(text))
            if event.topic_id != self.topic.currentText():
                raise ValueError("Event must match the selected topic")
            append_jsonl(self.path() / "known_events.jsonl", event)
            self.load_rows()
        except (ValueError, TypeError, KeyError) as exc:
            self.error(exc)

    def export_series(self) -> None:
        """Export plotted time series to CSV or JSON."""
        if ": " not in self.metric.currentText():
            return
        kind, _ = self.metric.currentText().split(": ", 1)
        rows = self.rows[kind]
        event = self.event.currentData()
        if self.relative.isChecked() and event:
            rows = EventEvaluator().aligned(rows, event)
        path, _ = QFileDialog.getSaveFileName(
            self, "Export", str(self.root / (kind + ".json")), "JSON (*.json);;CSV (*.csv)"
        )
        if not path:
            return
        if Path(path).suffix.lower() == ".csv":
            with open(path, "w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
                writer.writeheader()
                writer.writerows(rows)
        else:
            write_json(Path(path), rows)
