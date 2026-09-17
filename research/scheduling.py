from __future__ import annotations

from dataclasses import dataclass

from research.domain import CandidateTopic


@dataclass(frozen=True)
class TopicSelection:
    topic_id: str
    reason: str
    priority_score: float
    recent_signal: float
    uncertainty: float
    quota_cost_estimate: float


class TopicScheduler:
    """Deterministic exploration quota and explainable exploitation; no random/ML policy."""

    def __init__(self, topics: tuple[CandidateTopic, ...], exploration_fraction=0.3):
        self.topics, self.fraction = topics, exploration_fraction
        self.round = 0
        self.counts = {t.topic_id: 0 for t in topics}
        self.signals = {t.topic_id: 0.0 for t in topics}
        self.last_round = {t.topic_id: -1 for t in topics}

    def choose(self, costs: dict[str, float]) -> TopicSelection:
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

    def feedback(self, topic_id: str, score: float | None):
        if score is not None:
            self.signals[topic_id] = max(0.0, min(score / 100, 1.0))
