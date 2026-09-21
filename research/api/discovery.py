"""YouTube discovery collector for paginated searches, deduplication, and video observation extraction."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from uuid import uuid4

from research.api.client import RequestCancelled, YouTubeApiError, YouTubeQuotaExceeded
from research.api.quota import QuotaStopped
from research.api.services import YouTubeSearchService, YouTubeVideoService
from research.core.domain import (
    CollectionBundle,
    CollectionMetadata,
    CollectionRequest,
    Status,
    VideoIdentity,
    VideoObservation,
    WindowResolution,
    fingerprint,
)
from research.core.time import iso, now_utc, utc

log = logging.getLogger(__name__)


def observation(item: dict, timestamp: str) -> VideoObservation:
    """Extract and validate video observation counters from raw YouTube video resource item.

    Args:
        item: Raw item dictionary from YouTube videos.list API.
        timestamp: ISO-8601 observation timestamp.

    Returns:
        A VideoObservation instance.

    Raises:
        ValueError: If cumulative counters are negative.
    """
    snippet, stats = item["snippet"], item.get("statistics", {})
    published = iso(utc(snippet["publishedAt"]))
    counters = [
        int(stats[k]) if stats.get(k) is not None else None
        for k in ("viewCount", "likeCount", "commentCount")
    ]
    if any(v is not None and v < 0 for v in counters):
        raise ValueError("Negative cumulative counter")
    return VideoObservation(
        VideoIdentity(item["id"], snippet["channelId"], published, snippet.get("title", "")),
        timestamp,
        *counters,
    )


class YouTubeDiscoveryCollector:
    """Performs paginated search, fetches detailed statistics, and extracts eligible observations."""

    def __init__(self, client) -> None:
        self.client = client
        self.search = YouTubeSearchService(client)
        self.videos = YouTubeVideoService(client)

    def collect(
        self,
        request: CollectionRequest,
        last_success: str | None = None,
        now=None,
        *,
        window: WindowResolution | None = None,
        minimum_video_age_hours: float = 0.0,
        minimum_views: int = 0,
    ) -> CollectionBundle:
        """Execute a full discovery cycle for a topic within effective time bounds.

        Args:
            request: Collection request specifying topic, pages, and bounds.
            last_success: Timestamp of previous successful collection.
            now: Optional override for current time.
            window: Precomputed window resolution.
            minimum_video_age_hours: Threshold for filtering out immature videos.
            minimum_views: Minimum view count threshold.

        Returns:
            CollectionBundle containing observations, discovery identities, and metadata.
        """
        now = now or now_utc()
        if window is None:
            start, end, effective = request.bounds(
                now, last_success, minimum_video_age_hours=minimum_video_age_hours
            )
        else:
            start, end = window.effective_from, window.effective_to
            effective = start
            if last_success and request.window_mode == "ROLLING":
                effective = max(start, utc(last_success) - timedelta(minutes=request.overlap_minutes))
            if effective >= end:
                raise ValueError("Discovery watermark is outside the current eligible window")
        started = time.monotonic()
        telemetry_start = len(self.client.telemetry)
        identities, observations, warnings = {}, [], []
        detail_video_ids = set()
        received = pages = requested = 0
        token = None
        tokens = set()
        status, truncated = Status.COMPLETE, False
        excluded_too_young = excluded_low_views = excluded_missing_views = 0
        try:
            for _ in range(request.max_pages):
                requested += 1
                page = self.search.page(
                    request.topic.canonical_name,
                    request.page_size,
                    request.order,
                    iso(effective),
                    iso(end),
                    token,
                )
                pages += 1
                for item in page.get("items", []):
                    received += 1
                    try:
                        snippet = item["snippet"]
                        identity = VideoIdentity(
                            item["id"]["videoId"],
                            snippet["channelId"],
                            iso(utc(snippet["publishedAt"])),
                            snippet.get("title", ""),
                        )
                        identities.setdefault(identity.video_id, identity)
                    except (KeyError, ValueError, TypeError):
                        warnings.append("Malformed discovery item omitted")
                        status = Status.PARTIAL
                log.info(
                    "page_collected topic_id=%s page=%s items=%s",
                    request.topic.topic_id,
                    pages,
                    len(page.get("items", [])),
                )
                token = page.get("nextPageToken")
                if not token:
                    break
                if token in tokens:
                    warnings.append("Repeated page token; pagination stopped")
                    status = Status.PARTIAL
                    break
                tokens.add(token)
            truncated = bool(token)
            ids = list(identities)
            for offset in range(0, len(ids), 50):
                details = self.videos.get_videos(ids[offset : offset + 50])
                timestamp = iso(now_utc())
                for item in details:
                    try:
                        detail_video_ids.add(item["id"])
                        row = observation(item, timestamp)
                        published = utc(row.identity.published_at)
                        if published < start or published > end:
                            excluded_too_young += 1
                            warnings.append(f"OUTSIDE_EFFECTIVE_WINDOW:{row.identity.video_id}")
                        elif row.views is None and minimum_views > 0:
                            excluded_missing_views += 1
                            warnings.append(f"MISSING_VIEW_COUNT:{row.identity.video_id}")
                        elif row.views is not None and row.views < minimum_views:
                            excluded_low_views += 1
                            warnings.append(f"LOW_VIEW_COUNT:{row.identity.video_id}")
                        else:
                            observations.append(row)
                    except (ValueError, KeyError, TypeError):
                        warnings.append("Malformed video statistics omitted")
            if len(detail_video_ids) < len(identities):
                status = Status.PARTIAL
                warnings.append("Some discovered videos are unavailable")
        except (QuotaStopped, YouTubeQuotaExceeded) as exc:
            status = Status.QUOTA_STOPPED
            warnings.append(str(exc))
        except RequestCancelled:
            status = Status.CANCELLED
        except YouTubeApiError as exc:
            status = Status.PARTIAL if pages else Status.FAILED
            warnings.append(str(exc))
        finished = now_utc()
        metadata = CollectionMetadata(
            batch_id=uuid4().hex,
            timestamp=iso(now),
            query=request.topic.canonical_name,
            requested_from=iso(window.requested_from if window else start),
            requested_to=iso(window.requested_to if window else end),
            effective_from=iso(effective),
            effective_to=iso(end),
            window_mode=request.window_mode,
            rolling_window_hours=(end - start).total_seconds() / 3600,
            page_size=request.page_size,
            max_pages=request.max_pages,
            pages_requested=requested,
            pages_received=pages,
            items_received=received,
            unique_video_count=len(identities),
            duplicates_removed=max(
                0, received - len(identities) - sum(w.startswith("Malformed discovery") for w in warnings)
            ),
            api_profile_name=self.client.profile,
            started_at=iso(now),
            finished_at=iso(finished),
            duration_ms=(time.monotonic() - started) * 1000,
            status=status,
            truncated=truncated,
            config_hash=fingerprint(request),
            aliases=request.topic.aliases,
            order=request.order,
            minimum_video_age_hours=minimum_video_age_hours,
            minimum_views=minimum_views,
            discovered_video_count=len(identities),
            eligible_video_count=len(observations),
            excluded_too_young_count=excluded_too_young,
            excluded_low_views_count=excluded_low_views,
            excluded_missing_views_count=excluded_missing_views,
        )
        log.info(
            "collection_completed batch_id=%s topic_id=%s status=%s unique=%s",
            metadata.batch_id,
            request.topic.topic_id,
            status,
            len(identities),
        )
        return CollectionBundle(
            request.topic,
            metadata,
            tuple(observations),
            tuple(identities.values()),
            tuple(self.client.telemetry[telemetry_start:]),
            tuple(warnings),
        )
