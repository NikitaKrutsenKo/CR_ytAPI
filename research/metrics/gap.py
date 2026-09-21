"""Gap Engine mathematical formulas, state tracking, and creator baseline enrichment."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean, median

from research.api.discovery import observation
from research.api.services import YouTubeVideoService
from research.core.domain import CollectionBundle, ExperimentConfig
from research.core.models import (
    CollectionInfo,
    MetricSnapshot,
    RawTopicVideo,
    RecentVideoStat,
    Topic,
    YouTubeBatch,
)
from research.core.time import age_hours, iso, now_utc, utc
from research.metrics.calculators import (
    MetricConfig,
    creator_median_views_rate,
    decay_factor,
    log_min_max_normalize,
    percentile_extreme_median,
    view_rate,
    weighted_state_update,
)
from research.storage.io import read_json, write_json
from research.storage.state import MetricsState, NormalizationBoundaryState

log = logging.getLogger(__name__)


class GapEngine:
    """Deterministic Gap Engine calculating Supply, Demand, and Gap Score.

    Mathematical Lineage:
        1. View Rates:
           view_rate = views / (max(age_hours, 0.0) + epsilon)

        2. Creator Authority:
           creator_rate = median({ baseline_views_i / (baseline_age_i + epsilon) })
           creator_authority = creator_rate / (topic_rate + epsilon)

        3. Creator Supply:
           supply_low, supply_high = percentile_extreme_median(creator_authorities, extreme_percent)
           normalized_auth = log_min_max_normalize(authority, supply_low, supply_high, epsilon)
           supply_batch_norm = mean(normalized_auth)
           supply_state = EMA(supply_state, supply_batch_norm)
           supply_norm = clip(supply_state, 0.0, 1.0)

        4. Engagement Rate (ER):
           er_batch = (sum(likes) + sum(comments)) / (sum(views) + epsilon)
           er_norm = log_min_max_normalize(er_batch, er_low_ema, er_high_ema, epsilon)

        5. Performance Ratio (PR):
           pr_video = view_rate / (creator_expected_rate + epsilon)
           pr_batch = mean(pr_video)
           pr_norm = log_min_max_normalize(pr_batch, pr_low_ema, pr_high_ema, epsilon)

        6. Demand:
           demand_batch = w_er * er_norm + w_pr * pr_norm
           demand_state = EMA(demand_state, demand_batch)
           demand_norm = clip(demand_state, 0.0, 1.0)

        7. Final Gap Score:
           gap_score = demand_norm * (1.0 - supply_norm)
    """

    def __init__(self, config: MetricConfig | None = None) -> None:
        self.config = config or MetricConfig()
        self.config.validate()

    def process(
        self,
        batch: YouTubeBatch,
        state: MetricsState | None = None,
    ) -> tuple[MetricSnapshot, MetricsState]:
        """Process a YouTubeBatch and return a MetricSnapshot and updated MetricsState.

        Formula execution:
            1. Calculate topic view rates and batch mean rate.
            2. Compute decay factor: decay = 2 ** (-delta_hours / half_life_hours).
            3. Update topic rate EMA.
            4. Compute creator authorities and normalized supply.
            5. Compute engagement rate (ER) and performance ratio (PR).
            6. Compute combined demand and final gap_score = demand_norm * (1 - supply_norm).
        """
        if not batch.videos:
            raise ValueError("Cannot calculate metrics for an empty batch")

        state = state or MetricsState()
        now = datetime.fromisoformat(batch.collection.current_time.replace("Z", "+00:00"))

        topic_view_rates = [
            view_rate(
                video.views,
                age_hours(video.published_at, now),
                self.config.epsilon,
            )
            for video in batch.videos
        ]
        topic_rate_batch = mean(topic_view_rates)

        delta_hours = self._delta_hours(state.last_update_time, batch.collection.current_time)
        decay = decay_factor(delta_hours, self.config.half_life_hours)

        total_views = sum(video.views for video in batch.videos)
        total_likes = sum(video.likes for video in batch.videos)
        total_comments = sum(video.comments for video in batch.videos)
        total_engagement = total_likes + total_comments
        creator_ids = {video.channel_id for video in batch.videos}

        if not state.initialized:
            topic_rate = topic_rate_batch
        else:
            topic_rate = weighted_state_update(
                state.topic_rate,
                topic_rate_batch,
                delta_hours,
                self.config.half_life_hours,
            )

        creator_rates = [
            creator_median_views_rate(video, self.config.epsilon)
            for video in batch.videos
        ]
        creator_authorities = [
            creator_rate / (topic_rate + self.config.epsilon)
            for creator_rate in creator_rates
        ]

        # 1. Extreme tail anchors for supply normalization
        supply_low_batch, supply_high_batch = percentile_extreme_median(
            creator_authorities,
            self.config.extreme_percent,
        )

        # 2. Update EMA boundaries
        supply_low, supply_high = self._update_boundaries(
            state.normalization["supply"].low,
            state.normalization["supply"].high,
            supply_low_batch,
            supply_high_batch,
            state.initialized,
            delta_hours,
        )

        # 3. Normalized supply
        normalized_authorities = [
            log_min_max_normalize(auth, supply_low, supply_high, self.config.epsilon)
            for auth in creator_authorities
        ]
        supply_batch_norm = mean(normalized_authorities)
        supply_batch = sum(creator_authorities)

        # 4. Exponential smoothing of supply
        if not state.initialized:
            supply = supply_batch_norm
        else:
            supply = weighted_state_update(
                state.supply,
                supply_batch_norm,
                delta_hours,
                self.config.half_life_hours,
            )

        supply_norm = max(0.0, min(1.0, supply))

        er_batch = self._engagement_rate(batch)
        expected_rates = creator_rates
        pr_batch = mean(
            view_rate(
                video.views,
                age_hours(video.published_at, now),
                self.config.epsilon,
            ) / (expected_rate + self.config.epsilon)
            for video, expected_rate in zip(batch.videos, expected_rates)
        )

        er_low_batch, er_high_batch = percentile_extreme_median(
            self._engagement_rates_per_video(batch),
            self.config.extreme_percent,
        )
        pr_values = self._performance_ratios_per_video(batch, now)
        pr_low_batch, pr_high_batch = percentile_extreme_median(
            pr_values,
            self.config.extreme_percent,
        )

        er_low, er_high = self._update_boundaries(
            state.normalization["er"].low,
            state.normalization["er"].high,
            er_low_batch,
            er_high_batch,
            state.initialized,
            delta_hours,
        )
        pr_low, pr_high = self._update_boundaries(
            state.normalization["pr"].low,
            state.normalization["pr"].high,
            pr_low_batch,
            pr_high_batch,
            state.initialized,
            delta_hours,
        )

        er_norm = log_min_max_normalize(er_batch, er_low, er_high, self.config.epsilon)
        pr_norm = log_min_max_normalize(pr_batch, pr_low, pr_high, self.config.epsilon)

        demand_batch = (
            self.config.demand_weight_er * er_norm
            + self.config.demand_weight_pr * pr_norm
        )
        if not state.initialized:
            demand = demand_batch
        else:
            demand = weighted_state_update(
                state.demand,
                demand_batch,
                delta_hours,
                self.config.half_life_hours,
            )

        demand_norm = max(0.0, min(1.0, demand))
        gap_score = demand_norm * (1.0 - supply_norm)

        timestamp = batch.collection.current_time
        snapshot = MetricSnapshot(
            schema_version="1.0",
            timestamp=timestamp,
            topic=batch.topic.name,
            batch_id=batch.collection.batch_id,
            batch_size=batch.collection.batch_size,
            creator_count=len(creator_ids),
            qualifying_video_count=len(batch.videos),
            total_views=total_views,
            total_likes=total_likes,
            total_comments=total_comments,
            total_engagement=total_engagement,
            mean_view_rate=mean(topic_view_rates),
            median_view_rate=median(topic_view_rates),
            mean_creator_views_rate=mean(creator_rates),
            median_creator_views_rate=median(creator_rates),
            mean_creator_authority=mean(creator_authorities),
            median_creator_authority=median(creator_authorities),
            max_creator_authority=max(creator_authorities),
            expected_views_rate_mean=mean(expected_rates),
            expected_views_rate_median=median(expected_rates),
            delta_hours=delta_hours,
            decay_factor=decay,
            topic_rate_batch=topic_rate_batch,
            topic_rate=topic_rate,
            er_batch=er_batch,
            er_norm=er_norm,
            er_low=er_low,
            er_high=er_high,
            pr_batch=pr_batch,
            pr_norm=pr_norm,
            pr_low=pr_low,
            pr_high=pr_high,
            supply_batch=supply_batch,
            supply=supply,
            supply_norm=supply_norm,
            supply_low=supply_low,
            supply_high=supply_high,
            demand_batch=demand_batch,
            demand=demand,
            demand_norm=demand_norm,
            gap_score=gap_score,
        )

        new_state = MetricsState(
            initialized=True,
            last_update_time=timestamp,
            topic_rate=topic_rate,
            supply=supply,
            demand=demand,
            normalization={
                "er": NormalizationBoundaryState(low=er_low, high=er_high),
                "pr": NormalizationBoundaryState(low=pr_low, high=pr_high),
                "supply": NormalizationBoundaryState(low=supply_low, high=supply_high),
            },
        )

        return snapshot, new_state

    def _engagement_rate(self, batch: YouTubeBatch) -> float:
        """Calculate batch aggregate engagement rate.

        Formula:
            er_batch = (sum(likes) + sum(comments)) / (sum(views) + epsilon)
        """
        total_views = sum(video.views for video in batch.videos)
        total_engagement = sum(video.likes + video.comments for video in batch.videos)
        return total_engagement / (total_views + self.config.epsilon)

    def _engagement_rates_per_video(self, batch: YouTubeBatch) -> list[float]:
        """Calculate individual engagement rate for each video in the batch."""
        return [
            (video.likes + video.comments) / (video.views + self.config.epsilon)
            for video in batch.videos
        ]

    def _performance_ratios_per_video(self, batch: YouTubeBatch, now: datetime) -> list[float]:
        """Calculate individual performance ratios (actual / expected) for each video.

        Formula:
            pr_i = view_rate(video_i) / (creator_expected_rate_i + epsilon)
        """
        values: list[float] = []
        for video in batch.videos:
            expected = creator_median_views_rate(video, self.config.epsilon)
            actual = view_rate(
                video.views,
                age_hours(video.published_at, now),
                self.config.epsilon,
            )
            values.append(actual / (expected + self.config.epsilon))
        return values

    def _delta_hours(self, previous: str | None, current: str) -> float:
        """Compute elapsed hours between two ISO-8601 timestamps."""
        if previous is None:
            return 0.0
        previous_time = datetime.fromisoformat(previous.replace("Z", "+00:00"))
        current_time = datetime.fromisoformat(current.replace("Z", "+00:00"))
        delta = (current_time - previous_time).total_seconds() / 3600.0
        if delta < 0:
            raise ValueError("Batch time cannot be earlier than state last_update_time")
        return delta

    def _update_boundaries(
        self,
        previous_low: float,
        previous_high: float,
        batch_low: float,
        batch_high: float,
        initialized: bool,
        delta_hours: float,
    ) -> tuple[float, float]:
        """Update normalization boundaries using exponential smoothing."""
        if not initialized:
            return batch_low, batch_high
        return (
            weighted_state_update(
                previous_low, batch_low, delta_hours, self.config.half_life_hours
            ),
            weighted_state_update(
                previous_high, batch_high, delta_hours, self.config.half_life_hours
            ),
        )


class GapEnricher:
    """Creator baseline enrichment cache with configurable TTL.

    Fetches creator upload playlists and recent baseline videos, excluding the current
    thematic batch to prevent circular baseline contamination.
    """

    def __init__(self, client, cache_path: Path, config: ExperimentConfig, max_entries: int = 1000) -> None:
        self.client = client
        self.cache_path = cache_path
        self.config = config
        self.max_entries = max_entries
        self.cache = read_json(cache_path) if cache_path.exists() else {}
        self._prune_cache()
        self.videos = YouTubeVideoService(client)

    def _prune_cache(self) -> None:
        """Prune oldest entries if creator cache exceeds maximum capacity."""
        if len(self.cache) <= self.max_entries:
            return
        sorted_channels = sorted(
            self.cache.keys(),
            key=lambda c: self.cache[c].get("last_refresh", ""),
        )
        to_remove = len(self.cache) - self.max_entries
        for channel in sorted_channels[:to_remove]:
            del self.cache[channel]

    def enrich(self, bundle: CollectionBundle) -> YouTubeBatch:
        """Enrich a CollectionBundle into a YouTubeBatch with recent creator baselines."""
        now = now_utc()
        channels = sorted({v.identity.channel_id for v in bundle.observations})
        excluded = {v.video_id for v in bundle.discovery}
        stale = [
            c
            for c in channels
            if c not in self.cache
            or now - utc(self.cache[c]["last_refresh"]) >= timedelta(hours=self.config.baseline_ttl_hours)
        ]
        uploads = {}
        for offset in range(0, len(stale), 50):
            response = self.client.get(
                "channels", {"part": "contentDetails", "id": ",".join(stale[offset : offset + 50])}
            )
            for item in response.get("items", []):
                uploads[item["id"]] = (
                    item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
                )
        selected = {}
        for channel in stale:
            ids, token = [], None
            for _ in range(self.config.baseline_pages):
                if not uploads.get(channel):
                    break
                params = {"part": "contentDetails", "playlistId": uploads[channel], "maxResults": 50}
                if token:
                    params["pageToken"] = token
                page = self.client.get("playlistItems", params)
                for item in page.get("items", []):
                    identifier = item.get("contentDetails", {}).get("videoId")
                    if identifier and identifier not in excluded and identifier not in ids:
                        ids.append(identifier)
                token = page.get("nextPageToken")
                if len(ids) >= self.config.baseline_max or not token:
                    break
            selected[channel] = ids[: self.config.baseline_max]
        details = {
            item["id"]: item
            for item in self.videos.get_videos_batched(v for ids in selected.values() for v in ids)
        }
        timestamp = iso(now_utc())
        for channel, identifiers in selected.items():
            records = []
            for identifier in identifiers:
                if identifier in details:
                    try:
                        records.append(observation(details[identifier], timestamp))
                    except (KeyError, ValueError, TypeError):
                        log.warning("baseline_video_invalid channel_id=%s video_id=%s", channel, identifier)
            self.cache[channel] = {
                "uploads_playlist_id": uploads.get(channel),
                "recent_video_ids": identifiers,
                "last_refresh": timestamp,
                "observations": records,
            }
        self._prune_cache()
        write_json(self.cache_path, self.cache, compact=True)
        self.cache = read_json(self.cache_path)
        videos = []
        for current in bundle.observations:
            identity = current.identity
            baseline = []
            for previous in self.cache.get(identity.channel_id, {}).get("observations", []):
                if previous["identity"]["video_id"] in excluded or previous["views"] is None:
                    continue
                age = (
                    utc(previous["timestamp"]) - utc(previous["identity"]["published_at"])
                ).total_seconds() / 3600
                baseline.append(
                    RecentVideoStat(previous["identity"]["video_id"], max(age, 0), previous["views"])
                )
            if len(baseline) < self.config.baseline_min or any(
                v is None for v in (current.views, current.likes, current.comments)
            ):
                continue
            videos.append(
                RawTopicVideo(
                    identity.video_id,
                    identity.channel_id,
                    identity.published_at,
                    current.views,
                    current.likes,
                    current.comments,
                    len(baseline),
                    baseline,
                )
            )
        return YouTubeBatch(
            "1.0",
            Topic(bundle.topic.topic_id, bundle.topic.canonical_name),
            CollectionInfo(bundle.metadata.finished_at, bundle.metadata.batch_id, len(videos)),
            videos,
        )
