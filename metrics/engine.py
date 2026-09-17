from __future__ import annotations

from datetime import datetime
from statistics import mean, median

from metrics.calculators import (
    MetricConfig,
    creator_median_views_rate,
    decay_factor,
    log_min_max_normalize,
    percentile_extreme_median,
    view_rate,
    weighted_state_update,
)
from models.batch import YouTubeBatch
from models.metrics import MetricSnapshot
from state.manager import MetricsState
from models.time_utils import age_hours


class MetricEngine:
    def __init__(self, config: MetricConfig | None = None) -> None:
        self.config = config or MetricConfig()
        self.config.validate()

    def process(
        self,
        batch: YouTubeBatch,
        state: MetricsState | None = None,
    ) -> tuple[MetricSnapshot, MetricsState]:
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

        # 1. Опори для саплаю визначаємо з масиву авторитетивностей авторів у батчі
        supply_low_batch, supply_high_batch = percentile_extreme_median(
            creator_authorities,
            self.config.extreme_percent,
        )

        # 2. Оновлення меж стейту через EMA
        supply_low, supply_high = self._update_boundaries(
            state.normalization["supply"].low,
            state.normalization["supply"].high,
            supply_low_batch,
            supply_high_batch,
            state.initialized,
            delta_hours,
        )

        # 3. Нормалізація кожної авторитетності та знаходження середнього саплаю для батчу в [0, 1]
        normalized_authorities = [
            log_min_max_normalize(auth, supply_low, supply_high, self.config.epsilon)
            for auth in creator_authorities
        ]
        supply_batch_norm = mean(normalized_authorities)
        supply_batch = sum(creator_authorities)  # Сирий сумарний саплай для сніпшота

        # 4. Експоненційне згладжування нормалізованого саплаю
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
                "er": self._boundary_state(er_low, er_high),
                "pr": self._boundary_state(pr_low, pr_high),
                "supply": self._boundary_state(supply_low, supply_high),
            },
        )

        return snapshot, new_state

    def _engagement_rate(self, batch: YouTubeBatch) -> float:
        total_views = sum(video.views for video in batch.videos)
        total_engagement = sum(video.likes + video.comments for video in batch.videos)
        return total_engagement / (total_views + self.config.epsilon)

    def _engagement_rates_per_video(self, batch: YouTubeBatch) -> list[float]:
        return [
            (video.likes + video.comments) / (video.views + self.config.epsilon)
            for video in batch.videos
        ]

    def _performance_ratios_per_video(self, batch: YouTubeBatch, now: datetime) -> list[float]:
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

    @staticmethod
    def _boundary_state(low: float, high: float):
        from state.manager import NormalizationBoundaryState
        return NormalizationBoundaryState(low=low, high=high)
