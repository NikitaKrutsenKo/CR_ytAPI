"""Analytics visualization panel: PyQtGraph timeseries, event overlays, and data export."""

from __future__ import annotations

import csv
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from research.core.domain import KnownEvent
from research.core.time import iso, now_utc, utc
from research.metrics.evaluation import EventEvaluator
from research.storage.db import DatabaseStorageAdapter
from research.storage.io import append_jsonl, read_json, read_jsonl, write_json


class AnalyticsPanel(QWidget):
    """Interactive plotting panel for metric timeseries, known event alignment, and export."""

    def __init__(self, root: Path, db: DatabaseStorageAdapter | None = None) -> None:
        super().__init__()
        self.root = root
        try:
            self.db = db or DatabaseStorageAdapter()
        except Exception:
            self.db = None
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.events: list[KnownEvent] = []
        self._all_topics: list[Any] = []

        layout = QVBoxLayout(self)

        # Toolbar: Category, Search, Topic, Metric, Overlay
        toolbar = QHBoxLayout()
        self.category = QComboBox()
        self.topic_search = QLineEdit()
        self.topic_search.setPlaceholderText("Filter topics...")
        self.topic_search.setMaximumWidth(150)
        self.topic = QComboBox()
        self.metric = QComboBox()
        self.second = QComboBox()
        self.refresh_button = QPushButton("Refresh")

        # Backward compatibility alias
        self.experiment = self.category

        toolbar.addWidget(QLabel("Category:"))
        toolbar.addWidget(self.category)
        toolbar.addWidget(QLabel("Search:"))
        toolbar.addWidget(self.topic_search)
        toolbar.addWidget(QLabel("Topic:"))
        toolbar.addWidget(self.topic)
        toolbar.addWidget(QLabel("Metric:"))
        toolbar.addWidget(self.metric)
        toolbar.addWidget(QLabel("Overlay:"))
        toolbar.addWidget(self.second)
        toolbar.addWidget(self.refresh_button)
        layout.addLayout(toolbar)

        # Topic Metadata Banner
        self.topic_banner = QLabel("No topic selected")
        self.topic_banner.setStyleSheet(
            "QLabel { background-color: #1a2738; color: #e1e7ec; padding: 6px 12px; "
            "border-radius: 6px; font-size: 11px; margin-top: 2px; margin-bottom: 4px; }"
        )
        layout.addWidget(self.topic_banner)

        # Events & controls bar
        events_bar = QHBoxLayout()
        self.event = QComboBox()
        self.relative = QCheckBox("Hours relative to event")
        self.show_events = QCheckBox("Show event markers")
        self.show_events.setChecked(True)
        self.import_events = QPushButton("Import events JSON")
        self.add_event = QPushButton("Add verified event")
        self.export = QPushButton("Export series")

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

        # PyQtGraph Plot
        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(utcOffset=0)})
        self.plot.setBackground("#111c2d")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        layout.addWidget(self.plot, 1)

        # Details summary
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(160)
        layout.addWidget(self.details)

        # Signal connections
        self.refresh_button.clicked.connect(self.refresh)
        self.category.currentTextChanged.connect(self.on_category_changed)
        self.topic_search.textChanged.connect(self.filter_topics)
        self.topic.currentTextChanged.connect(self.on_topic_changed)
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

    def add_experiment(self, run_id: str) -> None:
        """Refresh topics and view upon run completion."""
        self.refresh()

    def refresh(self) -> None:
        """Reload categories and topics from PostgreSQL database."""
        selected_cat = self.category.currentText()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem("All Categories")

        categories = []
        if self.db:
            try:
                for c in self.db.list_categories():
                    cat_name = c["id"] if isinstance(c, dict) else getattr(c, "id", str(c))
                    if cat_name not in categories:
                        categories.append(cat_name)
            except Exception:
                pass
        for cat in sorted(categories):
            self.category.addItem(cat)

        if selected_cat and self.category.findText(selected_cat) >= 0:
            self.category.setCurrentText(selected_cat)
        self.category.blockSignals(False)

        # Load all topics from DB
        self._all_topics = []
        if self.db:
            try:
                self._all_topics = self.db.list_topics()
            except Exception:
                self._all_topics = []

        self.filter_topics()

    def on_category_changed(self) -> None:
        """Filter topics when category selection changes."""
        self.filter_topics()

    def filter_topics(self) -> None:
        """Filter topics dropdown based on active category and search filter."""
        selected_topic = self.topic.currentText()
        selected_category = self.category.currentText()
        search_query = self.topic_search.text().strip().lower()

        self.topic.blockSignals(True)
        self.topic.clear()

        matching = []
        for t in self._all_topics:
            t_id = t.id if hasattr(t, "id") else t.get("id")
            t_cat = (t.category_id if hasattr(t, "category_id") else t.get("category_id")) or ""
            t_name = (t.canonical_name if hasattr(t, "canonical_name") else t.get("canonical_name")) or t_id

            if selected_category != "All Categories" and t_cat != selected_category:
                continue
            if search_query and (search_query not in t_name.lower() and search_query not in t_id.lower()):
                continue
            matching.append(t_id)

        for t_id in matching:
            self.topic.addItem(t_id)

        if selected_topic and self.topic.findText(selected_topic) >= 0:
            self.topic.setCurrentText(selected_topic)
        elif self.topic.count() > 0:
            self.topic.setCurrentIndex(0)
        self.topic.blockSignals(False)
        self.on_topic_changed()

    def on_topic_changed(self) -> None:
        """Update topic metadata banner and load metric timeseries."""
        topic_id = self.topic.currentText()
        if not topic_id:
            self.topic_banner.setText("No topic selected")
            self.load_rows()
            return

        meta_topic = None
        for t in self._all_topics:
            tid = t.id if hasattr(t, "id") else t.get("id")
            if tid == topic_id:
                meta_topic = t
                break

        canonical = (
            meta_topic.canonical_name if hasattr(meta_topic, "canonical_name") else meta_topic.get("canonical_name", topic_id)
        ) if meta_topic else topic_id
        category = (
            meta_topic.category_id if hasattr(meta_topic, "category_id") else meta_topic.get("category_id", "Custom")
        ) if meta_topic else "Custom"
        lifecycle = (
            meta_topic.lifecycle_state if hasattr(meta_topic, "lifecycle_state") else meta_topic.get("lifecycle_state", "WATCHING")
        ) if meta_topic else "WATCHING"
        cadence = (
            meta_topic.cadence if hasattr(meta_topic, "cadence") else meta_topic.get("cadence", "REGULAR")
        ) if meta_topic else "REGULAR"

        colors = {
            "BREAKOUT": ("#0f5132", "#75b798"),
            "RISING": ("#084298", "#6ea8fe"),
            "WATCHING": ("#41464b", "#a7acb1"),
            "PEAK": ("#664d03", "#ffda6a"),
            "COOLING": ("#52182d", "#ea868f"),
        }
        bg, fg = colors.get(lifecycle, ("#343a40", "#dee2e6"))

        latest_trend = self.db.get_latest_trend(topic_id) if self.db else None
        latest_gap = self.db.get_latest_gap(topic_id) if self.db else None

        trend_val = None
        if latest_trend:
            trend_val = latest_trend.get("youtube_trend_score")
            if trend_val is None:
                trend_val = latest_trend.get("trend_score")
        trend_score = f"{trend_val:.1f}" if trend_val is not None else "N/A"
        velocity = f"{latest_trend['velocity']:.3f}" if latest_trend and latest_trend.get("velocity") is not None else "N/A"
        gap_val = latest_gap.get("gap_score") if latest_gap else None
        gap_score = f"{gap_val:.3f}" if gap_val is not None else "N/A"

        self.topic_banner.setText(
            f"<b>{canonical}</b> ({topic_id}) &nbsp;|&nbsp; "
            f"Category: <b>{category}</b> &nbsp;|&nbsp; "
            f"<span style='background-color:{bg}; color:{fg}; padding:2px 8px; border-radius:4px; font-weight:bold;'>{lifecycle}</span> &nbsp;|&nbsp; "
            f"Cadence: <b>{cadence}</b> &nbsp;|&nbsp; "
            f"Trend: <b>{trend_score}</b> &nbsp;|&nbsp; "
            f"Velocity: <b>{velocity}</b> &nbsp;|&nbsp; "
            f"Gap: <b>{gap_score}</b>"
        )
        self.load_rows()

    def path(self) -> Path:
        """Return root path for compatibility."""
        return self.root

    def load_rows(self) -> None:
        """Load metric history records for the selected topic from PostgreSQL."""
        self.rows = {}
        topic_id = self.topic.currentText()
        if not topic_id:
            self.draw()
            return

        if self.db:
            try:
                trend_metrics = self.db.get_trend_history(topic_id)
                if trend_metrics:
                    rows_trend = []
                    for m in trend_metrics:
                        ts = m["timestamp"]
                        if isinstance(ts, datetime) and ts.tzinfo is None:
                            ts = ts.replace(tzinfo=UTC)
                        trend_val = m.get("youtube_trend_score")
                        if trend_val is None:
                            trend_val = m.get("trend_score")
                        row = {
                            "timestamp": ts,
                            "trend_score": trend_val,
                            "youtube_trend_score": trend_val,
                            "velocity": m.get("velocity"),
                            "acceleration": m.get("acceleration"),
                        }
                        if isinstance(m.get("details"), dict):
                            for k, v in m["details"].items():
                                if k not in row:
                                    row[k] = v
                        rows_trend.append(row)
                    self.rows["trend"] = rows_trend

                gap_metrics = self.db.get_gap_history(topic_id)
                if gap_metrics:
                    rows_gap = []
                    for g in gap_metrics:
                        ts = g["timestamp"]
                        if isinstance(ts, datetime) and ts.tzinfo is None:
                            ts = ts.replace(tzinfo=UTC)
                        row = {
                            "timestamp": ts,
                            "gap_score": g.get("gap_score"),
                            "demand_norm": g.get("demand_norm"),
                            "supply_norm": g.get("supply_norm"),
                            "creator_authority": g.get("creator_authority"),
                        }
                        if isinstance(g.get("details"), dict):
                            for k, v in g["details"].items():
                                if k not in row:
                                    row[k] = v
                        rows_gap.append(row)
                    self.rows["gap"] = rows_gap
            except Exception as exc:
                self.error(exc)

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
            "trend: youtube_trend_score",
            "trend: youtube_trend_score_public",
            "trend: youtube_research_score",
            "trend: youtube_activity_count",
            "trend: growth_G_YT",
            "trend: burst_Z_YT",
            "trend: engagement_E_YT",
            "trend: breadth_B_YT",
            "gap: gap_score",
            "historical: sampled_publication_count",
        ):
            if self.metric.findText(preferred) >= 0:
                self.metric.setCurrentText(preferred)
                break

        self.event.blockSignals(True)
        self.event.clear()
        for event in self.events:
            if event.topic_id == topic_id:
                self.event.addItem(event.event_name, event)
        self.event.blockSignals(False)
        self.draw()

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
        self.details.setPlainText(json.dumps(details, default=str, ensure_ascii=False, indent=2))

    def import_known_events(self) -> None:
        """Prompt user for a JSON file of KnownEvent items."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Verified KnownEvent array", str(self.root), "JSON (*.json)"
        )
        if not path:
            return
        try:
            events = [KnownEvent(**v) for v in read_json(Path(path))]
            self.events.extend(events)
            self.on_topic_changed()
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
            self.events.append(event)
            self.on_topic_changed()
        except (ValueError, TypeError, KeyError) as exc:
            self.error(exc)

    def export_series(self) -> None:
        """Export plotted time series to CSV or JSON."""
        if ": " not in self.metric.currentText():
            return
        kind, _ = self.metric.currentText().split(": ", 1)
        rows = self.rows.get(kind, [])
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
            write_json(Path(path), rows, default=str)
