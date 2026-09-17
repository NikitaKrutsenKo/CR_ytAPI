from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
import re
from typing import Any
from uuid import uuid4


def utc(value: datetime | str) -> datetime:
    """Require an explicit timezone and normalize every internal timestamp to UTC."""
    value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if value.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


def encode(value: Any) -> Any:
    """Convert typed contracts to strict JSON; non-finite values are rejected."""
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
    return sha256(json.dumps(encode(value), sort_keys=True, allow_nan=False).encode()).hexdigest()


class Mode(StrEnum):
    GAP = "Quick Gap Test"
    TREND = "Quick Trend Test"
    COMBINED = "Combined Research"
    PRODUCT = "Product Simulation"
    HISTORICAL = "Historical Event Validation"


class Status(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    QUOTA_STOPPED = "QUOTA_STOPPED"


@dataclass(frozen=True)
class CandidateTopic:
    """Stable identity; aliases do not silently create extra API searches."""
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
        name = " ".join(name.split())
        if not name:
            raise ValueError("Topic cannot be empty")
        slug = re.sub(r"[^\w-]+", "_", name.lower()).strip("_")[:60] or "topic"
        return cls(slug + "_" + sha256(name.casefold().encode()).hexdigest()[:8], name)


@dataclass(frozen=True)
class CollectionRequest:
    """Static bounds or rolling window, plus incremental discovery overlap."""
    topic: CandidateTopic
    window_mode: str = "ROLLING"
    rolling_hours: float = 24.0
    requested_from: str | None = None
    requested_to: str | None = None
    page_size: int = 50
    max_pages: int = 1
    overlap_minutes: float = 5.0
    order: str = "date"

    def bounds(self, now: datetime, last_success: str | None = None) -> tuple[datetime, datetime, datetime]:
        now = utc(now)
        if not 1 <= self.page_size <= 50 or self.max_pages < 1:
            raise ValueError("Page size must be 1..50 and max pages positive")
        if self.window_mode == "STATIC":
            start, end = utc(self.requested_from or ""), utc(self.requested_to or "")
        elif self.window_mode == "ROLLING":
            if not math.isfinite(self.rolling_hours) or self.rolling_hours <= 0:
                raise ValueError("Rolling hours must be positive and finite")
            start, end = now - timedelta(hours=self.rolling_hours), now
        else:
            raise ValueError("Unknown window mode")
        if start >= end or self.overlap_minutes < 0:
            raise ValueError("Invalid collection time window")
        effective = start
        if last_success and self.window_mode == "ROLLING":
            effective = max(start, utc(last_success) - timedelta(minutes=self.overlap_minutes))
        if effective >= end:
            raise ValueError("Discovery watermark is in the future")
        return start, end, effective


@dataclass(frozen=True)
class VideoIdentity:
    video_id: str
    channel_id: str
    published_at: str
    title: str = ""


@dataclass(frozen=True)
class VideoObservation:
    """One immutable observation. Missing optional counters remain None, never zero."""
    identity: VideoIdentity
    timestamp: str
    views: int | None
    likes: int | None
    comments: int | None


@dataclass(frozen=True)
class CollectionMetadata:
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


@dataclass(frozen=True)
class QuotaTelemetry:
    endpoint: str
    timestamp: str
    api_profile_name: str
    estimated_cost: float
    status: str
    items_received: int = 0
    duration_ms: float = 0.0
    number_of_calls: int = 1
    pages: int = 0


@dataclass(frozen=True)
class CollectionBundle:
    """Shared, unfiltered ingestion output; enrichment is persisted separately."""
    topic: CandidateTopic
    metadata: CollectionMetadata
    observations: tuple[VideoObservation, ...]
    discovery: tuple[VideoIdentity, ...] = ()
    telemetry: tuple[QuotaTelemetry, ...] = ()
    warnings: tuple[str, ...] = ()
    schema_version: str = "2.0"

    @classmethod
    def from_dict(cls, raw: dict) -> CollectionBundle:
        if raw.get("schema_version") != "2.0":
            raise ValueError("Unsupported raw schema; use legacy replay for schema 1.0")
        metadata = dict(raw["metadata"])
        metadata["status"] = Status(metadata["status"])
        return cls(CandidateTopic(**raw["topic"]), CollectionMetadata(**metadata),
                   tuple(VideoObservation(VideoIdentity(**v["identity"]), v["timestamp"],
                                          v["views"], v["likes"], v["comments"])
                         for v in raw["observations"]),
                   tuple(VideoIdentity(**v) for v in raw.get("discovery", [])),
                   tuple(QuotaTelemetry(**v) for v in raw.get("telemetry", [])),
                   tuple(raw.get("warnings", [])))


@dataclass(frozen=True)
class ExperimentConfig:
    """Immutable run plan, with formula snapshots rather than mutable file references."""
    mode: Mode
    requests: tuple[CollectionRequest, ...]
    api_profile_name: str = "DEFAULT"
    duration_minutes: float = 1.0
    discovery_minutes: float = 30.0
    tracking_minutes: float = 15.0
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
        if not self.requests or len({r.topic.topic_id for r in self.requests}) != len(self.requests):
            raise ValueError("Provide at least one distinct topic")
        for request in self.requests:
            request.bounds(now_utc())
        for value in (self.duration_minutes, self.discovery_minutes, self.tracking_minutes, self.baseline_ttl_hours):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Durations and intervals must be positive and finite")
        if not 1 <= self.baseline_min <= self.baseline_max <= 50 or self.baseline_pages < 1:
            raise ValueError("Invalid creator baseline settings")
        if not 0 <= self.exploration_fraction <= 1 or not 0 <= self.reserve_percent < 100:
            raise ValueError("Invalid allocation or reserve")
        if min(self.search_budget, self.other_budget, self.tracked_limit) < 1 or not 0 <= self.max_retries <= 5:
            raise ValueError("Invalid budget, tracked limit or retry count")
        if self.mode == Mode.HISTORICAL and any(r.window_mode != "STATIC" for r in self.requests):
            raise ValueError("Historical validation requires a static publication window")

    @classmethod
    def from_dict(cls, raw: dict) -> ExperimentConfig:
        raw = dict(raw)
        raw["mode"] = Mode(raw["mode"])
        raw["requests"] = tuple(CollectionRequest(**{**r, "topic": CandidateTopic(**r["topic"])}) for r in raw["requests"])
        result = cls(**raw)
        result.validate()
        return result


@dataclass(frozen=True)
class KnownEvent:
    """Evaluation-only ground truth. Never passed into either metric engine."""
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
        if not self.source_url.startswith(("https://", "http://")):
            raise ValueError("Known events require a traceable source URL")
