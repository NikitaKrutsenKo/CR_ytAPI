from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from research.domain import ExperimentConfig, Mode, now_utc


class QuotaStopped(RuntimeError):
    """A request was blocked before touching the network."""


@dataclass(frozen=True)
class QuotaEstimate:
    search_calls: int
    other_units: int
    status: str
    label: str = "Local estimate (worst case including retries)"
    endless: bool = False
    search_calls_per_hour: float | None = None
    other_units_per_hour: float | None = None
    search_calls_per_pacific_day: int | None = None
    other_units_per_pacific_day: int | None = None


class QuotaManager:
    """Atomic per-profile daily ledger, with separate search and other read pools.

    Counts every attempt before sending it, including failed/retried requests.
    Pacific calendar days match the API reset day. Never rotates credentials.
    SQLite transactions coordinate concurrent application instances.
    """

    def __init__(
        self,
        path: Path,
        profile: str,
        search_budget=100,
        other_budget=10000,
        reserve_percent=10.0,
        clock=now_utc,
    ):
        self.path, self.profile, self.clock = Path(path), profile, clock
        self.limits = (search_budget, other_budget)
        self.reserve = reserve_percent
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS usage (profile TEXT, day TEXT, endpoint TEXT, calls INTEGER, PRIMARY KEY(profile,day,endpoint))"
            )

    def _day(self) -> str:
        return self.clock().astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()

    def usage(self) -> dict:
        with sqlite3.connect(self.path) as db:
            rows = dict(
                db.execute(
                    "SELECT endpoint,calls FROM usage WHERE profile=? AND day=?", (self.profile, self._day())
                )
            )
        return {
            "label": "Local estimate",
            "api_profile_name": self.profile,
            "day": self._day(),
            "search_calls_used": rows.get("search", 0),
            "other_units_estimated": sum(n for key, n in rows.items() if key != "search"),
            "search_calls_budget": self.limits[0],
            "other_units_budget": self.limits[1],
            "reserve_percent": self.reserve,
            "api_calls_by_endpoint": rows,
            "actual_local_call_count": sum(rows.values()),
        }

    def next_reset(self):
        pacific = ZoneInfo("America/Los_Angeles")
        local = self.clock().astimezone(pacific)
        next_day = local.date() + timedelta(days=1)
        return datetime.combine(next_day, time.min, pacific).astimezone(ZoneInfo("UTC"))

    def seconds_until_reset(self) -> float:
        return max(0.0, (self.next_reset() - self.clock()).total_seconds())

    def consume(self, endpoint: str) -> None:
        if endpoint not in {"search", "videos", "channels", "playlistItems"}:
            raise ValueError("Unknown endpoint quota cost")
        day = self._day()
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = dict(
                db.execute("SELECT endpoint,calls FROM usage WHERE profile=? AND day=?", (self.profile, day))
            )
            used = (
                rows.get("search", 0)
                if endpoint == "search"
                else sum(n for k, n in rows.items() if k != "search")
            )
            limit = self.limits[0 if endpoint == "search" else 1] * (1 - self.reserve / 100)
            if used + 1 > limit:
                raise QuotaStopped(f"Quota safety stop: {endpoint}; local estimate")
            db.execute(
                "INSERT INTO usage VALUES (?,?,?,1) ON CONFLICT(profile,day,endpoint) DO UPDATE SET calls=calls+1",
                (self.profile, day, endpoint),
            )

    def estimate(self, config: ExperimentConfig) -> QuotaEstimate:
        """Conservative full-run plan: cold creator caches and all retries each cycle."""
        config.validate()
        duration_minutes = config.duration_hours * 60
        cycles = (
            1
            if config.mode == Mode.HISTORICAL
            else math.ceil(duration_minutes / config.discovery_minutes)
        )
        if config.endless_mode:
            cycles = max(1, math.ceil(60 / config.discovery_minutes))
        requests = config.requests
        if config.mode == Mode.PRODUCT:
            requests = (max(requests, key=lambda r: r.page_size * r.max_pages),)
        search = cycles * (
            max(r.max_pages for r in config.requests)
            if config.mode == Mode.PRODUCT
            else sum(r.max_pages for r in requests)
        )
        other = cycles * sum(math.ceil(r.page_size * r.max_pages / 50) for r in requests)
        if config.mode in (Mode.GAP, Mode.COMBINED, Mode.PRODUCT):
            creators = sum(r.page_size * r.max_pages for r in requests)
            other += cycles * (
                math.ceil(creators / 50)
                + creators * config.baseline_pages
                + math.ceil(creators * config.baseline_max / 50)
            )
        if config.mode != Mode.GAP and config.mode != Mode.HISTORICAL:
            tracking_minutes = 60 if config.endless_mode else duration_minutes
            tracking_cycles = max(0, math.ceil(tracking_minutes / config.tracking_minutes) - 1)
            other += tracking_cycles * len(config.requests) * math.ceil(config.tracked_limit / 50)
        search *= config.max_retries + 1
        other *= config.max_retries + 1
        if config.endless_mode:
            search_per_hour, other_per_hour = float(search), float(other)
            search, other = math.ceil(search_per_hour * 24), math.ceil(other_per_hour * 24)
        used = self.usage()
        ratios = [
            (search + used["search_calls_used"]) / (self.limits[0] * (1 - self.reserve / 100)),
            (other + used["other_units_estimated"]) / (self.limits[1] * (1 - self.reserve / 100)),
        ]
        status = "WOULD EXCEED BUDGET" if max(ratios) > 1 else "WARNING" if max(ratios) > 0.8 else "SAFE"
        if config.endless_mode:
            return QuotaEstimate(
                search,
                other,
                status,
                "Endless local estimate per Pacific day (collection pauses at quota reset)",
                True,
                search_per_hour,
                other_per_hour,
                search,
                other,
            )
        return QuotaEstimate(search, other, status)
