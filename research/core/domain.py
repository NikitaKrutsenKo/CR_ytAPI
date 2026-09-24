"""Core domain entities, experiment contracts, and collection models."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Any

from research.core.time import iso, now_utc, utc


def encode(value: Any) -> Any:
    """Convert typed contracts and dataclasses to strict JSON-compatible data structures.

    Rejects non-finite values (NaN, Inf) and converts datetimes to UTC ISO strings.

    Args:
        value: Any Python object, dataclass, or collection.

    Returns:
        JSON-serializable representation.
    """
    if isinstance(value, datetime):
        return iso(value)
    if hasattr(value, "__dataclass_fields__"):
        return {k: encode(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [encode(v) for v in value]
    return value


def fingerprint(value: Any) -> str:
    """Compute a deterministic SHA-256 hex digest for a configuration or payload.

    Args:
        value: Serializable data object.

    Returns:
        Hex-encoded SHA-256 hash string.
    """
    return sha256(json.dumps(encode(value), sort_keys=True, allow_nan=False).encode()).hexdigest()


class Mode(StrEnum):
    """Experiment execution mode."""

    GAP = "Quick Gap Test"
    TREND = "Quick Trend Test"
    COMBINED = "Combined Research"
    PRODUCT = "Product Simulation"
    HISTORICAL = "Historical Event Validation"


class Status(StrEnum):
    """Execution and collection completion status."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    QUOTA_STOPPED = "QUOTA_STOPPED"
    WAITING_FOR_QUOTA = "WAITING_FOR_QUOTA"


@dataclass(frozen=True)
class CandidateTopic:
    """Stable topic identity and tracking metadata.

    Attributes:
        topic_id: Unique topic hash or identifier.
        canonical_name: Canonical topic query string.
        aliases: Alternative search queries mapping to the same topic.
        category: Broad category name (e.g. 'Gaming', 'AI', 'Custom').
        source: Provenance source (e.g. 'manual', 'pool').
        created_at: ISO timestamp of creation.
        priority: Scheduling priority weight.
        monitoring_state: Lifecycle state for product scheduling.
    """

    topic_id: str
    canonical_name: str
    aliases: tuple[str, ...] = ()
    category: str = "Custom"
    source: str = "manual"
    created_at: str = field(default_factory=lambda: iso(now_utc()))
    priority: float = 1.0
    monitoring_state: str = "WATCHING"

    @classmethod
    def named(cls, name: str) -> CandidateTopic:
        """Create a CandidateTopic with a deterministic ID from its name."""
        name = " ".join(name.split())
        if not name:
            raise ValueError("Topic cannot be empty")
        slug = re.sub(r"[^\w-]+", "_", name.lower()).strip("_")[:60] or "topic"
        return cls(slug + "_" + sha256(name.casefold().encode()).hexdigest()[:8], name)


@dataclass(frozen=True)
class WindowResolution:
    """Resolved effective publication time bounds after eligibility gating.

    Attributes:
        requested_from: Requested lower bound.
        requested_to: Requested upper bound.
        effective_from: Actual lower bound passed to search API.
        effective_to: Actual upper bound passed to search API.
        latest_allowed_to: Maximum timestamp allowed by minimum video age.
        window_width_seconds: Duration of the publication window in seconds.
        minimum_video_age_hours: Required minimum video maturation age.
        was_capped: True if requested_to was clamped by minimum video age.
        cap_reason: Explanation if clamping occurred.
        elapsed_seconds: Monotonic elapsed seconds for rolling window advancement.
    """

    requested_from: datetime
    requested_to: datetime
    effective_from: datetime
    effective_to: datetime
    latest_allowed_to: datetime
    window_width_seconds: float
    minimum_video_age_hours: float
    was_capped: bool
    cap_reason: str | None = None
    elapsed_seconds: float = 0.0


class WindowResolver:
    """Policy for computing and shifting publication search windows."""

    @staticmethod
    def resolve(
        request: CollectionRequest,
        now: datetime,
        minimum_video_age_hours: float,
        *,
        elapsed_seconds: float = 0.0,
        initial: WindowResolution | None = None,
    ) -> WindowResolution:
        """Resolve publication time bounds ensuring minimum video age eligibility.

        For ROLLING windows, advances bounds monotonically by elapsed_seconds while
        preserving the exact window width.
        """
        now = utc(now)
        if not math.isfinite(minimum_video_age_hours) or minimum_video_age_hours < 0:
            raise ValueError("Minimum video age must be non-negative and finite")
        if request.window_mode not in {"STATIC", "ROLLING"}:
            raise ValueError("Unknown window mode")
        if initial is not None:
            delta = timedelta(seconds=elapsed_seconds if request.window_mode == "ROLLING" else 0)
            return WindowResolution(
                initial.requested_from,
                initial.requested_to,
                initial.effective_from + delta,
                initial.effective_to + delta,
                now - timedelta(hours=minimum_video_age_hours),
                initial.window_width_seconds,
                minimum_video_age_hours,
                initial.was_capped,
                initial.cap_reason,
                elapsed_seconds,
            )
        latest = now - timedelta(hours=minimum_video_age_hours)
        if request.window_mode == "ROLLING" and (not request.requested_from or not request.requested_to):
            end, start = latest, latest - timedelta(hours=24)
        else:
            start, end = utc(request.requested_from or ""), utc(request.requested_to or "")
        if start >= end:
            raise ValueError("From must be earlier than To")
        width = (end - start).total_seconds()
        capped = end > latest
        effective_to = min(end, latest)
        effective_from = (
            effective_to - timedelta(seconds=width) if capped and request.window_mode == "ROLLING" else start
        )
        if effective_from >= effective_to:
            raise ValueError("Effective From must be earlier than effective To after minimum-age capping")
        reason = (
            f"Selected To was capped by minimum video age ({minimum_video_age_hours:g}h); "
            + (
                "the entire rolling range was shifted to preserve its width"
                if request.window_mode == "ROLLING"
                else "From remains fixed"
            )
            if capped
            else None
        )
        return WindowResolution(
            start,
            end,
            effective_from,
            effective_to,
            latest,
            width,
            minimum_video_age_hours,
            capped,
            reason,
        )


@dataclass(frozen=True)
class CollectionRequest:
    """Parameters for collecting videos for a topic within a publication window."""

    topic: CandidateTopic
    window_mode: str = "ROLLING"
    requested_from: str | None = None
    requested_to: str | None = None
    page_size: int = 50
    max_pages: int = 1
    overlap_minutes: float = 5.0
    order: str = "date"

    def bounds(
        self,
        now: datetime,
        last_success: str | None = None,
        minimum_video_age_hours: float = 0.0,
        elapsed_seconds: float = 0.0,
        initial: WindowResolution | None = None,
    ) -> tuple[datetime, datetime, datetime]:
        """Compute effective (start, end, incremental_start) boundaries for collection."""
        resolved = WindowResolver.resolve(
            self, now, minimum_video_age_hours, elapsed_seconds=elapsed_seconds, initial=initial
        )
        start, end = resolved.effective_from, resolved.effective_to
        if not 1 <= self.page_size <= 50 or self.max_pages < 1:
            raise ValueError("Page size must be 1..50 and max pages positive")
        if self.overlap_minutes < 0:
            raise ValueError("Invalid collection time window")
        effective = start
        if last_success and self.window_mode == "ROLLING":
            effective = max(start, utc(last_success) - timedelta(minutes=self.overlap_minutes))
        if effective >= end:
            raise ValueError("Discovery watermark is in the future")
        return start, end, effective


@dataclass(frozen=True)
class VideoIdentity:
    """Immutable identity of a YouTube video."""

    video_id: str
    channel_id: str
    published_at: str
    title: str = ""


@dataclass(frozen=True)
class VideoObservation:
    """Point-in-time observation of counters for a video. Missing counters remain None."""

    identity: VideoIdentity
    timestamp: str
    views: int | None
    likes: int | None
    comments: int | None


@dataclass(frozen=True)
class CollectionMetadata:
    """Execution details, telemetry, and diagnostics for a collection operation."""

    batch_id: str
    timestamp: str
    query: str
    requested_from: str
    requested_to: str
    effective_from: str
    effective_to: str
    window_mode: str
    rolling_window_hours: float
    page_size: int
    max_pages: int
    pages_requested: int
    pages_received: int
    items_received: int
    unique_video_count: int
    duplicates_removed: int
    api_profile_name: str
    started_at: str
    finished_at: str
    duration_ms: float
    status: Status
    truncated: bool = False
    operation: str = "discovery"
    collector_version: str = "discovery_v2"
    config_hash: str = ""
    aliases: tuple[str, ...] = ()
    order: str = "date"
    api_calls_by_endpoint: dict[str, int] = field(default_factory=dict)
    actual_local_call_count: int = 0
    estimated_quota_cost: dict[str, int] = field(default_factory=dict)
    trend_formula_version: str = ""
    gap_formula_version: str = ""
    minimum_video_age_hours: float = 0.0
    minimum_views: int = 0
    discovered_video_count: int = 0
    eligible_video_count: int = 0
    excluded_too_young_count: int = 0
    excluded_low_views_count: int = 0
    excluded_missing_views_count: int = 0


@dataclass(frozen=True)
class QuotaTelemetry:
    """Record of an API request and its estimated quota cost."""

    endpoint: str
    timestamp: str
    api_profile_name: str
    estimated_cost: float
    status: str
    items_received: int = 0
    duration_ms: float = 0.0
    number_of_calls: int = 1
    pages: int = 0
    experiment_id: str = ""
    topic_id: str = ""
    operation: str = ""


@dataclass(frozen=True)
class CollectionBundle:
    """Shared, unfiltered collection output preserving raw observations."""

    topic: CandidateTopic
    metadata: CollectionMetadata
    observations: tuple[VideoObservation, ...]
    discovery: tuple[VideoIdentity, ...] = ()
    telemetry: tuple[QuotaTelemetry, ...] = ()
    warnings: tuple[str, ...] = ()
    schema_version: str = "2.0"

    @classmethod
    def from_dict(cls, raw: dict) -> CollectionBundle:
        """Deserialize raw collection dictionary into a CollectionBundle."""
        if raw.get("schema_version") != "2.0":
            raise ValueError("Unsupported raw schema; use legacy replay for schema 1.0")
        metadata = dict(raw["metadata"])
        metadata["status"] = Status(metadata["status"])
        return cls(
            CandidateTopic(**raw["topic"]),
            CollectionMetadata(**metadata),
            tuple(
                VideoObservation(
                    VideoIdentity(**v["identity"]), v["timestamp"], v["views"], v["likes"], v["comments"]
                )
                for v in raw["observations"]
            ),
            tuple(VideoIdentity(**v) for v in raw.get("discovery", [])),
            tuple(QuotaTelemetry(**v) for v in raw.get("telemetry", [])),
            tuple(raw.get("warnings", [])),
        )


@dataclass(frozen=True)
class ExperimentConfig:
    """Immutable experiment plan with snapshot formula parameters."""

    mode: Mode
    requests: tuple[CollectionRequest, ...]
    api_profile_name: str = "DEFAULT"
    duration_hours: float = 1.0
    discovery_minutes: float = 30.0
    tracking_minutes: float = 15.0
    endless_mode: bool = False
    minimum_video_age_hours: float = 1.0
    minimum_views: int = 1000
    baseline_min: int = 5
    baseline_max: int = 10
    baseline_ttl_hours: float = 12.0
    baseline_pages: int = 2
    tracked_limit: int = 100
    exploration_fraction: float = 0.3
    search_budget: int = 100
    other_budget: int = 10000
    reserve_percent: float = 10.0
    max_retries: int = 2
    gap_formula: dict = field(default_factory=dict)
    trend_formula: dict = field(default_factory=dict)

    def validate(self) -> None:
        """Validate all experiment constraints before execution."""
        if not self.requests or len({r.topic.topic_id for r in self.requests}) != len(self.requests):
            raise ValueError("Provide at least one distinct topic")
        for request in self.requests:
            request.bounds(now_utc(), minimum_video_age_hours=self.minimum_video_age_hours)
        for value in (
            self.discovery_minutes,
            self.tracking_minutes,
            self.baseline_ttl_hours,
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Durations and intervals must be positive and finite")
        if not self.endless_mode and (not math.isfinite(self.duration_hours) or self.duration_hours <= 0):
            raise ValueError("Duration hours must be positive and finite for a finite experiment")
        if not math.isfinite(self.minimum_video_age_hours) or self.minimum_video_age_hours < 0:
            raise ValueError("Minimum video age must be non-negative and finite")
        if (
            isinstance(self.minimum_views, bool)
            or not isinstance(self.minimum_views, int)
            or self.minimum_views < 0
        ):
            raise ValueError("Minimum views must be a non-negative integer")
        if not 1 <= self.baseline_min <= self.baseline_max <= 50 or self.baseline_pages < 1:
            raise ValueError("Invalid creator baseline settings")
        if not 0 <= self.exploration_fraction <= 1 or not 0 <= self.reserve_percent < 100:
            raise ValueError("Invalid allocation or reserve")
        if (
            min(self.search_budget, self.other_budget, self.tracked_limit) < 1
            or not 0 <= self.max_retries <= 5
        ):
            raise ValueError("Invalid budget, tracked limit or retry count")
        if self.mode == Mode.HISTORICAL and any(r.window_mode != "STATIC" for r in self.requests):
            raise ValueError("Historical validation requires a static publication window")

    @classmethod
    def from_dict(cls, raw: dict) -> ExperimentConfig:
        """Deserialize and migrate experiment configuration dictionary."""
        raw = dict(raw)
        if "duration_hours" not in raw:
            raw["duration_hours"] = float(raw.pop("duration_minutes", 60.0)) / 60.0
        else:
            raw.pop("duration_minutes", None)
        minimum_age = float(raw.get("minimum_video_age_hours", 1.0))
        raw["mode"] = Mode(raw["mode"])
        requests = []
        for value in raw["requests"]:
            request = dict(value)
            legacy_hours = request.pop("rolling_hours", None)
            if request.get("window_mode") == "ROLLING" and (
                not request.get("requested_from") or not request.get("requested_to")
            ):
                hours = float(legacy_hours if legacy_hours is not None else 24.0)
                end = now_utc() - timedelta(hours=minimum_age)
                request["requested_to"] = iso(end)
                request["requested_from"] = iso(end - timedelta(hours=hours))
            request["topic"] = CandidateTopic(**request["topic"])
            requests.append(CollectionRequest(**request))
        raw["requests"] = tuple(requests)
        result = cls(**raw)
        result.validate()
        return result


@dataclass(frozen=True)
class KnownEvent:
    """Manually verified real-world ground truth event for evaluation."""

    topic_id: str
    event_name: str
    event_type: str
    event_time: str
    description: str
    source_url: str
    source_type: str
    verified_at: str

    def __post_init__(self) -> None:
        utc(self.event_time)
        utc(self.verified_at)
        if not self.event_name.strip() or not self.topic_id.strip():
            raise ValueError("Known event requires a name and topic ID")
        if not self.source_url.startswith(("https://", "http://")):
            raise ValueError("Known events require a traceable source URL")
