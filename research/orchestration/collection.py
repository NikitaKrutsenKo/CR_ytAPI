"""Coordination service for discovery collection and tracking refreshes."""

from __future__ import annotations

import logging
from uuid import uuid4

from research.api.client import RequestCancelled, YouTubeApiError, YouTubeQuotaExceeded
from research.api.discovery import YouTubeDiscoveryCollector, observation
from research.api.quota import QuotaStopped
from research.api.services import YouTubeVideoService
from research.core.domain import (
    CollectionBundle,
    CollectionMetadata,
    CollectionRequest,
    Status,
    WindowResolution,
)
from research.core.time import iso, now_utc

log = logging.getLogger(__name__)


class CollectionService:
    """Coordinates discovery and counter tracking operations, maintaining watermarks."""

    def __init__(self, client) -> None:
        self.collector = YouTubeDiscoveryCollector(client)
        self.client = client
        self.watermarks: dict[str, str] = {}

    def discover(
        self,
        request: CollectionRequest,
        window: WindowResolution | None = None,
        minimum_video_age_hours: float = 0.0,
        minimum_views: int = 0,
    ) -> CollectionBundle:
        """Run a discovery collection cycle; advances watermark only upon complete collection."""
        bundle = self.collector.collect(
            request,
            self.watermarks.get(request.topic.topic_id),
            window=window,
            minimum_video_age_hours=minimum_video_age_hours,
            minimum_views=minimum_views,
        )
        if bundle.metadata.status == Status.COMPLETE and not bundle.metadata.truncated:
            self.watermarks[request.topic.topic_id] = bundle.metadata.effective_to
        return bundle

    def track(self, request: CollectionRequest, identifiers: list[str] | tuple[str, ...]) -> CollectionBundle:
        """Fetch updated counters for previously registered video IDs using cheap videos.list."""
        now = now_utc()
        rows, warnings = [], []
        status = Status.COMPLETE
        telemetry_start = len(self.client.telemetry)
        try:
            ids = list(identifiers)
            for offset in range(0, len(ids), 50):
                for item in YouTubeVideoService(self.client).get_videos(ids[offset : offset + 50]):
                    try:
                        rows.append(observation(item, iso(now_utc())))
                    except (KeyError, ValueError, TypeError):
                        warnings.append("Malformed tracking observation")
            if len(rows) < len(ids):
                status = Status.PARTIAL
                warnings.append("Some tracked videos unavailable")
        except (QuotaStopped, YouTubeQuotaExceeded) as exc:
            status = Status.QUOTA_STOPPED
            warnings.append(str(exc))
        except RequestCancelled:
            status = Status.CANCELLED
        except YouTubeApiError as exc:
            status = Status.PARTIAL if rows else Status.FAILED
            warnings.append(str(exc))
        finished = now_utc()
        meta = CollectionMetadata(
            batch_id=uuid4().hex,
            timestamp=iso(now),
            query=request.topic.canonical_name,
            requested_from=iso(now),
            requested_to=iso(now),
            effective_from=iso(now),
            effective_to=iso(now),
            window_mode="TRACKING",
            rolling_window_hours=0,
            page_size=50,
            max_pages=0,
            pages_requested=0,
            pages_received=0,
            items_received=len(rows),
            unique_video_count=len(rows),
            duplicates_removed=0,
            api_profile_name=self.client.profile,
            started_at=iso(now),
            finished_at=iso(finished),
            duration_ms=(finished - now).total_seconds() * 1000,
            status=status,
            operation="tracking",
        )
        return CollectionBundle(
            request.topic,
            meta,
            tuple(rows),
            telemetry=tuple(self.client.telemetry[telemetry_start:]),
            warnings=tuple(warnings),
        )
