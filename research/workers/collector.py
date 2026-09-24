"""Stateless Ingestion Worker daemon with atomic leasing and adaptive quota cadence.

Coordinates topic discovery, tracking, trend calculation, and dynamic lifecycle
transitions across horizontally scalable worker processes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from threading import Event
from typing import Any
from uuid import uuid4

from research.api.client import RequestCancelled, YouTubeQuotaExceeded
from research.api.quota import QuotaStopped
from research.core.domain import CandidateTopic, CollectionBundle, CollectionRequest
from research.core.time import now_utc, utc
from research.metrics.tracking import TrackedVideoRegistry
from research.metrics.trend import TrendConfig, TrendEngine
from research.orchestration.collection import CollectionService
from research.storage.db import DatabaseStorageAdapter

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CadenceSchedule:
    """Discovery and tracking recurrence intervals in seconds."""

    discovery_interval_seconds: float
    tracking_interval_seconds: float


CADENCE_MAP: dict[str, CadenceSchedule] = {
    "FAST": CadenceSchedule(discovery_interval_seconds=15 * 60, tracking_interval_seconds=5 * 60),
    "REGULAR": CadenceSchedule(discovery_interval_seconds=2 * 3600, tracking_interval_seconds=15 * 60),
    "SLOW": CadenceSchedule(discovery_interval_seconds=12 * 3600, tracking_interval_seconds=60 * 60),
}


def resolve_effective_cadence(assigned_cadence: str, search_remaining_percent: float) -> str:
    """Adaptively step down discovery cadence when remaining daily search quota is low.

    Rules:
    - If search quota == 0%: discovery halts ('THROTTLED_HALT').
    - If search quota <= 20%: FAST -> REGULAR, REGULAR -> SLOW.
    - Otherwise: retain assigned cadence.
    """
    if search_remaining_percent <= 0.0:
        return "THROTTLED_HALT"
    if search_remaining_percent <= 20.0:
        if assigned_cadence == "FAST":
            return "REGULAR"
        if assigned_cadence == "REGULAR":
            return "SLOW"
    return assigned_cadence


class IngestionWorker:
    """Stateless worker daemon for executing topic collection and metric updates."""

    def __init__(
        self,
        db: DatabaseStorageAdapter,
        client: Any,
        worker_id: str | None = None,
        profile: str = "DEFAULT",
        search_budget: int = 100,
        lease_duration_seconds: int = 300,
        trend_config: TrendConfig | None = None,
        cadence_map: dict[str, CadenceSchedule] | None = None,
    ) -> None:
        self.db = db
        self.client = client
        self.worker_id = worker_id or f"worker_{uuid4().hex[:8]}"
        self.profile = profile
        self.search_budget = search_budget
        self.lease_duration_seconds = lease_duration_seconds
        self.trend_config = trend_config or TrendConfig()
        self.cadences = cadence_map or CADENCE_MAP
        self.collection_service = CollectionService(client)

        # In-memory transient state per worker (can be reconstructed or re-instantiated)
        self.registries: dict[str, TrackedVideoRegistry] = {}
        self.trend_engines: dict[str, TrendEngine] = {}

    def _get_registry(self, topic_id: str) -> TrackedVideoRegistry:
        if topic_id not in self.registries:
            self.registries[topic_id] = TrackedVideoRegistry(limit=100)
        return self.registries[topic_id]

    def _get_trend_engine(self, topic_id: str) -> TrendEngine:
        if topic_id not in self.trend_engines:
            engine = TrendEngine(self.trend_config)
            # Hydrate state from latest db trend record if exists
            latest = self.db.get_latest_trend(topic_id)
            if latest and latest.get("details"):
                details = latest["details"]
                engine.state.lifecycle = latest.get("lifecycle_state", "WATCHING")
                engine.state.ewma = latest.get("ewma")
                engine.state.velocity = latest.get("velocity")
                engine.state.valid_windows = details.get("valid_windows", 0)
                engine.state.valid_duration_hours = details.get("effective_observation_duration_hours", 0.0)
            self.trend_engines[topic_id] = engine
        return self.trend_engines[topic_id]

    def step(self) -> dict[str, Any] | None:
        """Attempt to claim a due topic and execute one collection cycle.

        Returns telemetry dict if a topic was processed, or None if no topics were due.
        """
        topic = self.db.claim_due_topic(self.worker_id, self.lease_duration_seconds)
        if not topic:
            return None

        now = now_utc()
        search_pct = self.db.get_remaining_search_quota_percent(self.profile, self.search_budget)
        effective_cadence = resolve_effective_cadence(topic.cadence, search_pct)
        schedule = self.cadences.get(effective_cadence, self.cadences.get("REGULAR", CADENCE_MAP["REGULAR"]))

        operation = "discovery"
        discovery_due = utc(topic.next_discovery_at) <= now
        tracking_due = utc(topic.next_tracking_at) <= now

        # If discovery is halted by quota, fall back to tracking if possible
        if effective_cadence == "THROTTLED_HALT":
            if tracking_due:
                operation = "tracking"
            else:
                # Reschedule discovery to 1 hour ahead or reset
                next_discovery = now + timedelta(hours=1)
                self.db.release_topic(
                    topic.id,
                    self.worker_id,
                    next_discovery_at=next_discovery,
                    lifecycle_reason="Throttled by daily search quota exhaustion",
                )
                return {
                    "worker_id": self.worker_id,
                    "topic_id": topic.id,
                    "status": "SKIPPED_QUOTA_EXHAUSTION",
                }
        elif discovery_due:
            operation = "discovery"
        elif tracking_due:
            operation = "tracking"

        bundle: CollectionBundle | None = None
        new_lifecycle_state = topic.lifecycle_state
        new_lifecycle_reason = topic.lifecycle_reason
        new_cadence = topic.cadence

        try:
            candidate = CandidateTopic(
                topic_id=topic.id,
                canonical_name=topic.canonical_name,
                aliases=topic.aliases,
                category=topic.category_id or "Custom",
                priority=topic.priority,
                monitoring_state=topic.lifecycle_state,
            )
            request = CollectionRequest(topic=candidate, page_size=50, max_pages=1)

            if operation == "discovery":
                bundle = self.collection_service.discover(request)
                self.db.save_bundle(bundle, operation="discovery", status=bundle.metadata.status.value)

                # Mathematical processing
                registry = self._get_registry(topic.id)
                trend_engine = self._get_trend_engine(topic.id)
                deltas = registry.observe(bundle.observations, baseline=trend_engine.baseline)
                trend_engine.engagement(deltas)

                trend_step = trend_engine.process(bundle)
                if trend_step:
                    self.db.save_trend_metric(
                        topic_id=topic.id,
                        batch_id=bundle.metadata.batch_id,
                        timestamp=bundle.metadata.timestamp,
                        trend_row=trend_step,
                    )

                    new_lifecycle_state = trend_step.get("lifecycle_state", topic.lifecycle_state)
                    new_lifecycle_reason = trend_step.get("lifecycle_reason", topic.lifecycle_reason)

                    # Dynamic Cadence escalation/de-escalation feedback loop
                    if new_lifecycle_state == "BREAKOUT" and topic.cadence != "FAST":
                        new_cadence = "FAST"
                    elif new_lifecycle_state in ("COOLING", "PEAK") and topic.cadence == "FAST":
                        new_cadence = "REGULAR"

            else:  # tracking
                registry = self._get_registry(topic.id)
                tracked_ids = [k for k in registry.videos.keys()]
                bundle = self.collection_service.track(request, tracked_ids)
                self.db.save_bundle(bundle, operation="tracking", status=bundle.metadata.status.value)

                trend_engine = self._get_trend_engine(topic.id)
                deltas = registry.observe(bundle.observations, baseline=trend_engine.baseline)
                trend_engine.engagement(deltas)

            # Compute next scheduled timestamps
            next_disc = now + timedelta(seconds=schedule.discovery_interval_seconds)
            next_track = now + timedelta(seconds=schedule.tracking_interval_seconds)

            self.db.release_topic(
                topic_id=topic.id,
                worker_id=self.worker_id,
                next_discovery_at=next_disc,
                next_tracking_at=next_track,
                lifecycle_state=new_lifecycle_state,
                lifecycle_reason=new_lifecycle_reason,
                cadence=new_cadence,
            )

            return {
                "worker_id": self.worker_id,
                "topic_id": topic.id,
                "operation": operation,
                "status": bundle.metadata.status.value if bundle else "UNKNOWN",
                "lifecycle_state": new_lifecycle_state,
                "effective_cadence": effective_cadence,
                "cadence": new_cadence,
            }

        except (QuotaStopped, YouTubeQuotaExceeded) as exc:
            log.warning("Quota stopped on topic %s: %s", topic.id, exc)
            self.db.release_topic(
                topic_id=topic.id,
                worker_id=self.worker_id,
                next_discovery_at=now + timedelta(hours=2),
                lifecycle_reason=f"Quota stop: {exc}",
            )
            return {"worker_id": self.worker_id, "topic_id": topic.id, "status": "QUOTA_STOPPED"}
        except RequestCancelled:
            self.db.release_topic(topic_id=topic.id, worker_id=self.worker_id)
            return {"worker_id": self.worker_id, "topic_id": topic.id, "status": "CANCELLED"}
        except Exception as exc:
            log.exception("Unexpected error in worker %s on topic %s", self.worker_id, topic.id)
            self.db.release_topic(
                topic_id=topic.id,
                worker_id=self.worker_id,
                next_discovery_at=now + timedelta(minutes=5),
                lifecycle_reason=f"Worker error: {exc}",
            )
            raise

    def run(self, stop_event: Event, poll_interval: float = 1.0) -> None:
        """Run the ingestion worker loop continuously until stop_event is set."""
        while not stop_event.is_set():
            result = self.step()
            if not result:
                stop_event.wait(poll_interval)
