"""Asynchronous NLP Triage daemon for topic classification and category routing."""

from __future__ import annotations

import logging
from threading import Event
from typing import Any

from research.nlp.classifier import SemanticCentroidClassifier, TopicClassifier
from research.storage.db import DatabaseStorageAdapter

log = logging.getLogger(__name__)


class NLPTriageWorker:
    """Asynchronous triage worker consuming PENDING_CLASSIFICATION topics.

    Ensures NLP classification is completely decoupled from YouTube collection loops,
    preventing any latency or inference overhead from blocking API ingestion.
    """

    def __init__(
        self,
        db: DatabaseStorageAdapter,
        classifier: TopicClassifier | None = None,
        model_version: str = "centroid_v1",
    ) -> None:
        self.db = db
        self.classifier = classifier or SemanticCentroidClassifier(version=model_version)
        self.model_version = model_version

    def step(self) -> list[dict[str, Any]]:
        """Find all PENDING_CLASSIFICATION topics, classify them, and promote to ACTIVE."""
        pending_topics = self.db.list_topics(status="PENDING_CLASSIFICATION")
        if not pending_topics:
            return []

        results = []
        for topic in pending_topics:
            classification = self.classifier.classify(
                topic_name=topic.canonical_name,
                context_text=topic.query,
            )

            self.db.update_topic_classification(
                topic_id=topic.id,
                category_id=classification.category_id,
                confidence=classification.confidence,
                model_version=self.model_version,
                candidate_aliases=classification.candidate_aliases,
            )

            results.append(
                {
                    "topic_id": topic.id,
                    "canonical_name": topic.canonical_name,
                    "category_id": classification.category_id,
                    "confidence": classification.confidence,
                }
            )
            log.info(
                "Triaged topic '%s' (%s) -> category '%s' (confidence: %.2f)",
                topic.canonical_name,
                topic.id,
                classification.category_id,
                classification.confidence,
            )

        return results

    def run(self, stop_event: Event, poll_interval: float = 2.0) -> None:
        """Run triage loop continuously until stop_event is set."""
        while not stop_event.is_set():
            self.step()
            stop_event.wait(poll_interval)
