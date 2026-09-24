"""Main App API Gateway: REST endpoints for dashboards, analytics, and topic subscriptions.

Provides HTTP JSON endpoints for categories, topics, time-series metrics, manual topic
registration (pending NLP triage), and cadence adjustments.
"""

from __future__ import annotations

import json
import logging
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from research.core.domain import CandidateTopic
from research.storage.db import DatabaseStorageAdapter

log = logging.getLogger(__name__)


class GatewayService:
    """Domain service handling Main App read gateway and write operations."""

    def __init__(self, db: DatabaseStorageAdapter) -> None:
        self.db = db

    def list_categories(self) -> list[dict[str, Any]]:
        """Return all active categories with topic counts and aggregate metrics."""
        categories = self.db.list_categories(status="ACTIVE")
        enriched = []
        for cat in categories:
            topics = self.db.list_topics(category_id=cat["id"], status="ACTIVE")
            enriched.append(
                {
                    "id": cat["id"],
                    "name": cat["name"],
                    "status": cat["status"],
                    "aggregate_momentum": cat["aggregate_momentum"],
                    "breakout_count": cat["breakout_count"],
                    "topic_count": len(topics),
                }
            )
        return enriched

    def list_category_topics(self, category_id: str) -> list[dict[str, Any]]:
        """Return all topics within a category enriched with latest trend scores."""
        topics = self.db.list_topics(category_id=category_id, status="ACTIVE")
        enriched = []
        for t in topics:
            latest_trend = self.db.get_latest_trend(t.id)
            latest_gap = self.db.get_latest_gap(t.id)
            enriched.append(
                {
                    "id": t.id,
                    "canonical_name": t.canonical_name,
                    "query": t.query,
                    "category_id": t.category_id,
                    "lifecycle_state": t.lifecycle_state,
                    "lifecycle_reason": t.lifecycle_reason,
                    "cadence": t.cadence,
                    "priority": t.priority,
                    "youtube_trend_score": latest_trend.get("youtube_trend_score") if latest_trend else None,
                    "youtube_trend_score_public": (
                        latest_trend.get("youtube_trend_score_public") if latest_trend else None
                    ),
                    "gap_score": latest_gap.get("gap_score") if latest_gap else None,
                    "updated_at": t.updated_at,
                }
            )
        return enriched

    def get_topic_metrics(self, topic_id: str, limit: int = 100) -> dict[str, Any]:
        """Return historical trend and gap snapshots for charting."""
        topic = self.db.get_topic(topic_id)
        if not topic:
            raise KeyError(f"Topic '{topic_id}' not found")

        trend_history = self.db.get_trend_history(topic_id, limit=limit)
        return {
            "topic_id": topic_id,
            "canonical_name": topic.canonical_name,
            "lifecycle_state": topic.lifecycle_state,
            "cadence": topic.cadence,
            "trend_history": trend_history,
        }

    def register_topic(
        self,
        canonical_name: str,
        query: str | None = None,
        aliases: tuple[str, ...] | list[str] | None = None,
    ) -> dict[str, Any]:
        """Register a new topic in PENDING_CLASSIFICATION state for NLP triage."""
        candidate = CandidateTopic.named(canonical_name)
        topic_id = candidate.topic_id
        resolved_query = query or canonical_name

        self.db.register_topic(
            topic_id=topic_id,
            canonical_name=candidate.canonical_name,
            query=resolved_query,
            category_id=None,
            aliases=aliases or (),
            status="PENDING_CLASSIFICATION",
            cadence="REGULAR",
            priority=1.0,
        )

        return {
            "topic_id": topic_id,
            "canonical_name": candidate.canonical_name,
            "query": resolved_query,
            "status": "PENDING_CLASSIFICATION",
            "message": "Topic registered successfully; awaiting NLP category classification.",
        }

    def update_cadence(self, topic_id: str, cadence: str) -> dict[str, Any]:
        """Manually pin or adjust topic cadence (e.g. 'FAST', 'REGULAR', 'SLOW', 'STOPPED')."""
        valid_cadences = {"FAST", "REGULAR", "SLOW", "STOPPED"}
        if cadence not in valid_cadences:
            raise ValueError(f"Invalid cadence '{cadence}'. Must be one of {sorted(valid_cadences)}")

        topic = self.db.get_topic(topic_id)
        if not topic:
            raise KeyError(f"Topic '{topic_id}' not found")

        with self.db.transaction() as conn:
            conn.execute("UPDATE topics SET cadence = %s WHERE id = %s", (cadence, topic_id))

        updated = self.db.get_topic(topic_id)
        return {
            "topic_id": topic_id,
            "canonical_name": updated.canonical_name if updated else "",
            "cadence": cadence,
            "status": updated.status if updated else "",
        }


def make_gateway_handler(service: GatewayService):
    """Create HTTP request handler bound to the GatewayService."""

    class GatewayHTTPHandler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")

            if path == "/api/categories":
                categories = service.list_categories()
                self._send_json(HTTPStatus.OK, categories)
                return

            cat_topics_match = re.fullmatch(r"/api/categories/([^/]+)/topics", path)
            if cat_topics_match:
                category_id = cat_topics_match.group(1)
                topics = service.list_category_topics(category_id)
                self._send_json(HTTPStatus.OK, topics)
                return

            metrics_match = re.fullmatch(r"/api/topics/([^/]+)/metrics", path)
            if metrics_match:
                topic_id = metrics_match.group(1)
                try:
                    metrics = service.get_topic_metrics(topic_id)
                    self._send_json(HTTPStatus.OK, metrics)
                except KeyError as exc:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Path '{path}' not found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")

            if path == "/api/topics":
                length = int(self.headers.get("Content-Length", 0))
                raw_body = self.rfile.read(length)
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                    name = payload.get("canonical_name")
                    if not name:
                        self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing 'canonical_name'"})
                        return
                    result = service.register_topic(
                        canonical_name=name,
                        query=payload.get("query"),
                        aliases=payload.get("aliases"),
                    )
                    self._send_json(HTTPStatus.CREATED, result)
                except Exception as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Path '{path}' not found"})

        def do_PATCH(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")

            cadence_match = re.fullmatch(r"/api/topics/([^/]+)/cadence", path)
            if cadence_match:
                topic_id = cadence_match.group(1)
                length = int(self.headers.get("Content-Length", 0))
                raw_body = self.rfile.read(length)
                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                    cadence = payload.get("cadence")
                    if not cadence:
                        self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing 'cadence'"})
                        return
                    result = service.update_cadence(topic_id, cadence)
                    self._send_json(HTTPStatus.OK, result)
                except KeyError as exc:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                except ValueError as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except Exception as exc:
                    self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Path '{path}' not found"})

        def log_message(self, format: str, *args: Any) -> None:
            # Suppress default noisy access logs during tests
            pass

    return GatewayHTTPHandler


def create_gateway_server(
    db: DatabaseStorageAdapter,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> ThreadingHTTPServer:
    """Construct a ThreadingHTTPServer instance for the API Gateway."""
    service = GatewayService(db)
    handler_class = make_gateway_handler(service)
    server = ThreadingHTTPServer((host, port), handler_class)
    return server
