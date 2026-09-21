"""Deterministic topic scheduling balancing exploration and monitoring."""

from __future__ import annotations

from dataclasses import dataclass

from research.core.domain import CandidateTopic


@dataclass(frozen=True)
class TopicSelection:
    """Decision record for selecting a candidate topic in a scheduling round.

    Attributes:
        topic_id: Selected topic identifier.
        reason: Justification ('exploration' or 'active_monitoring').
        priority_score: Computed scheduling priority score.
        recent_signal: Most recent normalized research signal.
        uncertainty: Inverse sample count uncertainty weight.
        quota_cost_estimate: Planned search quota expenditure.
    """

    topic_id: str
    reason: str
    priority_score: float
    recent_signal: float
    uncertainty: float
    quota_cost_estimate: float


class TopicScheduler:
    """Deterministic exploration quota and explainable monitoring scheduler without random/ML policy."""

    def __init__(self, topics: tuple[CandidateTopic, ...], exploration_fraction: float = 0.3) -> None:
        self.topics = topics
        self.fraction = exploration_fraction
        self.round = 0
        self.counts = {t.topic_id: 0 for t in topics}
        self.signals = {t.topic_id: 0.0 for t in topics}
        self.last_round = {t.topic_id: -1 for t in topics}

    def choose(self, costs: dict[str, float]) -> TopicSelection:
        """Select the next topic to collect based on priority, uncertainty, and cost."""
        self.round += 1
        exploration = int(self.round * self.fraction) > int((self.round - 1) * self.fraction)

        def priority(topic):
            identifier = topic.topic_id
            uncertainty = 1 / (1 + self.counts[identifier])
            waiting = (self.round - self.last_round[identifier]) / max(1, len(self.topics))
            score = (topic.priority + self.signals[identifier] + uncertainty + 0.1 * waiting) / max(
                costs[identifier], 1
            )
            return (-self.counts[identifier], score, identifier) if exploration else (score, identifier)

        topic = max(self.topics, key=priority)
        identifier = topic.topic_id
        uncertainty = 1 / (1 + self.counts[identifier])
        waiting = (self.round - self.last_round[identifier]) / max(1, len(self.topics))
        score = (topic.priority + self.signals[identifier] + uncertainty + 0.1 * waiting) / max(
            costs[identifier], 1
        )
        self.counts[identifier] += 1
        self.last_round[identifier] = self.round
        return TopicSelection(
            identifier,
            "exploration" if exploration else "active_monitoring",
            score,
            self.signals[identifier],
            uncertainty,
            costs[identifier],
        )

    def feedback(self, topic_id: str, score: float | None) -> None:
        """Update recent signal feedback for a topic after metric calculation."""
        if score is not None:
            self.signals[topic_id] = max(0.0, min(score / 100, 1.0))
