from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QDateTime, Qt, QTimeZone
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from research.domain import CandidateTopic, CollectionRequest, ExperimentConfig, Mode, iso
from research.storage import read_json, write_json


def number(value, low=0.01, high=100000, integer=False):
    widget = QSpinBox() if integer else QDoubleSpinBox()
    widget.setRange(low, high)
    widget.setValue(value)
    return widget


class ExperimentForm(QWidget):
    """Presentation-only form converting user input to validated domain contracts."""

    def __init__(self, root: Path, profiles):
        super().__init__()
        self.root, self.profiles = root, profiles
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        basic, advanced = QWidget(), QWidget()
        self.tabs.addTab(basic, "Experiment")
        self.tabs.addTab(advanced, "Settings & quota")
        form, settings = QFormLayout(basic), QFormLayout(advanced)
        self.profile = QComboBox()
        self.profile.addItems(profiles.names or ["DEFAULT"])
        self.profile.setCurrentText(profiles.default)
        self.mode = QComboBox()
        self.mode.addItems([m.value for m in Mode])
        self.topics = QLineEdit("AI laptop")
        self.topics.setPlaceholderText("Separate topics with semicolons")
        self.pool = QComboBox()
        self.pool.addItems(["Custom", "Gaming", "AI", "Tech", "Entertainment", "Science", "Finance"])
        self.pool.currentTextChanged.connect(self.choose_pool)
        self.window = QComboBox()
        self.window.addItems(["ROLLING", "STATIC"])
        self.hours = number(24, 0.1, 87600)
        self.start = QDateTimeEdit(QDateTime.currentDateTimeUtc().addDays(-7))
        self.end = QDateTimeEdit(QDateTime.currentDateTimeUtc())
        for field in (self.start, self.end):
            field.setDisplayFormat("yyyy-MM-dd HH:mm 'UTC'")
            field.setTimeZone(QTimeZone.utc())
            field.setCalendarPopup(True)
        self.pages = number(1, 1, 1000, True)
        self.page_size = number(50, 1, 50, True)
        self.duration = number(1, 0.01, 525600)
        self.discovery = number(30, 0.1, 10080)
        self.tracking = number(15, 0.1, 10080)
        for label, widget in (
            ("API profile", self.profile),
            ("Mode", self.mode),
            ("Topic pool", self.pool),
            ("Topics (semicolon separated)", self.topics),
            ("Window", self.window),
            ("Rolling hours", self.hours),
            ("From", self.start),
            ("To", self.end),
            ("Max pages", self.pages),
            ("Page size", self.page_size),
            ("Duration (minutes)", self.duration),
            ("Discovery interval (minutes)", self.discovery),
            ("Counter tracking (minutes)", self.tracking),
        ):
            form.addRow(label, widget)
        self.gap_formula, self.trend_formula = (
            QLineEdit("config/formulas/gap_v1.json"),
            QLineEdit("config/formulas/trend_balanced.json"),
        )
        self.minimum, self.maximum = number(5, 1, 50, True), number(10, 1, 50, True)
        self.ttl, self.baseline_pages = number(12, 0.1, 168), number(2, 1, 20, True)
        self.search_budget, self.other_budget = number(100, 1, 100000, True), number(10000, 1, 10000000, True)
        self.reserve, self.exploration = number(10, 0, 99), number(30, 0, 100)
        self.overlap = number(5, 0, 60)
        self.limit, self.retries = number(100, 1, 10000, True), number(2, 0, 5, True)
        for label, widget in (
            ("Gap formula JSON", self.gap_formula),
            ("Trend formula JSON", self.trend_formula),
            ("Baseline minimum videos", self.minimum),
            ("Baseline maximum videos", self.maximum),
            ("Baseline cache TTL (hours)", self.ttl),
            ("Baseline max playlist pages", self.baseline_pages),
            ("Search calls / Pacific day", self.search_budget),
            ("Other read units / Pacific day", self.other_budget),
            ("Safety reserve (%)", self.reserve),
            ("Exploration (%)", self.exploration),
            ("Discovery overlap (minutes)", self.overlap),
            ("Tracked videos per topic", self.limit),
            ("Max retries", self.retries),
        ):
            settings.addRow(label, widget)
        note = QLabel(
            "Quota values are local estimates. External project usage is not visible.\n"
            "Trend uses a named YouTube activity research formula; production multi-platform Trend Score is unavailable."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.window.currentTextChanged.connect(self.update_window)
        self.mode.currentTextChanged.connect(self.update_mode)
        self.update_window()

    def choose_pool(self, name):
        pools = {
            "Gaming": "GTA VI; Minecraft; Fortnite",
            "AI": "AI laptop; generative AI; AI agents",
            "Tech": "smartphones; GPUs; laptops",
            "Entertainment": "movies; music; streaming",
            "Science": "space exploration; robotics; astronomy",
            "Finance": "personal finance; budgeting; investing",
        }
        if name in pools:
            self.topics.setText(pools[name])

    def update_mode(self):
        if self.mode.currentText() == Mode.HISTORICAL:
            self.window.setCurrentText("STATIC")
        self.window.setEnabled(self.mode.currentText() != Mode.HISTORICAL)

    def update_window(self):
        static = self.window.currentText() == "STATIC"
        self.start.setEnabled(static)
        self.end.setEnabled(static)
        self.hours.setEnabled(not static)

    def config(self) -> ExperimentConfig:
        requests = []
        for name in self.topics.text().split(";"):
            topic = CandidateTopic.named(name)
            topic = CandidateTopic(**{**asdict(topic), "category": self.pool.currentText()})
            requests.append(
                CollectionRequest(
                    topic,
                    self.window.currentText(),
                    self.hours.value(),
                    iso(datetime.fromtimestamp(self.start.dateTime().toSecsSinceEpoch(), UTC)),
                    iso(datetime.fromtimestamp(self.end.dateTime().toSecsSinceEpoch(), UTC)),
                    self.page_size.value(),
                    self.pages.value(),
                    self.overlap.value(),
                )
            )
        config = ExperimentConfig(
            Mode(self.mode.currentText()),
            tuple(requests),
            self.profile.currentText(),
            self.duration.value(),
            self.discovery.value(),
            self.tracking.value(),
            self.minimum.value(),
            self.maximum.value(),
            self.ttl.value(),
            self.baseline_pages.value(),
            self.limit.value(),
            self.exploration.value() / 100,
            self.search_budget.value(),
            self.other_budget.value(),
            self.reserve.value(),
            self.retries.value(),
            read_json(self.root / self.gap_formula.text()),
            read_json(self.root / self.trend_formula.text()),
        )
        config.validate()
        return config

    def load_config(self, config: ExperimentConfig):
        self.mode.setCurrentText(config.mode)
        self.topics.setText("; ".join(r.topic.canonical_name for r in config.requests))
        self.profile.setCurrentText(config.api_profile_name)
        request = config.requests[0]
        self.window.setCurrentText(request.window_mode)
        self.hours.setValue(request.rolling_hours)
        if request.requested_from:
            self.start.setDateTime(QDateTime.fromString(request.requested_from, Qt.DateFormat.ISODate))
        if request.requested_to:
            self.end.setDateTime(QDateTime.fromString(request.requested_to, Qt.DateFormat.ISODate))
        for widget, value in (
            (self.pages, request.max_pages),
            (self.page_size, request.page_size),
            (self.overlap, request.overlap_minutes),
            (self.duration, config.duration_minutes),
            (self.discovery, config.discovery_minutes),
            (self.tracking, config.tracking_minutes),
            (self.minimum, config.baseline_min),
            (self.maximum, config.baseline_max),
            (self.ttl, config.baseline_ttl_hours),
            (self.baseline_pages, config.baseline_pages),
            (self.limit, config.tracked_limit),
            (self.exploration, config.exploration_fraction * 100),
            (self.search_budget, config.search_budget),
            (self.other_budget, config.other_budget),
            (self.reserve, config.reserve_percent),
            (self.retries, config.max_retries),
        ):
            widget.setValue(value)
        # Imported formulas are materialized separately; raw experiment config stays immutable.
        for kind, values, widget in (
            ("gap", config.gap_formula, self.gap_formula),
            ("trend", config.trend_formula, self.trend_formula),
        ):
            path = self.root / "data" / "imported_formulas" / (kind + ".json")
            write_json(path, values)
            widget.setText(str(path))
