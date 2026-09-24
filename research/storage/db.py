"""Relational database storage engine, atomic task leasing, and schema management.

Supports SQLite WAL mode for zero-configuration local deployment and provides
an architecture directly adaptable to PostgreSQL.
"""

from __future__ import annotations

import json
import math
import os
import psycopg
from psycopg.rows import dict_row
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.api.quota import QuotaEstimate, QuotaStopped
from research.core.domain import CollectionBundle, ExperimentConfig, Mode, encode
from research.core.time import iso, now_utc, utc

from dotenv import load_dotenv
load_dotenv()


POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_DB = os.getenv("POSTGRES_DB")

DB_PATH = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

POSTGRES_SCHEMA = """
-- Category Pool Table
CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'ACTIVE',
    aggregate_momentum DOUBLE PRECISION DEFAULT 0.0,
    breakout_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Topic State Machine Table
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY,
    category_id TEXT REFERENCES categories(id) ON DELETE SET NULL,
    canonical_name TEXT NOT NULL,
    query TEXT NOT NULL,
    aliases TEXT DEFAULT '[]',
    nlp_model_version TEXT,
    classification_confidence DOUBLE PRECISION,
    status TEXT DEFAULT 'ACTIVE',
    lifecycle_state TEXT DEFAULT 'WATCHING',
    lifecycle_reason TEXT DEFAULT 'Initial state',
    cadence TEXT DEFAULT 'REGULAR',
    priority DOUBLE PRECISION DEFAULT 1.0,
    next_discovery_at TEXT NOT NULL,
    next_tracking_at TEXT NOT NULL,
    locked_by TEXT,
    locked_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_topics_lease ON topics (status, cadence, next_discovery_at, priority);
CREATE INDEX IF NOT EXISTS idx_topics_category ON topics (category_id);

-- Immutable Raw Observations (100% Replay Fidelity)
CREATE TABLE IF NOT EXISTS raw_batches (
    batch_id TEXT PRIMARY KEY,
    topic_id TEXT REFERENCES topics(id),
    operation TEXT NOT NULL,
    window_from TEXT NOT NULL,
    window_to TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    telemetry TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_batches_topic_time ON raw_batches (topic_id, window_to);

-- Calculated Trend Time-Series
CREATE TABLE IF NOT EXISTS metrics_trend (
    id SERIAL PRIMARY KEY,
    topic_id TEXT REFERENCES topics(id),
    batch_id TEXT REFERENCES raw_batches(batch_id),
    timestamp TEXT NOT NULL,
    youtube_trend_score DOUBLE PRECISION,
    youtube_trend_score_public DOUBLE PRECISION,
    youtube_activity_count INTEGER,
    youtube_incidence DOUBLE PRECISION,
    ewma DOUBLE PRECISION,
    velocity DOUBLE PRECISION,
    acceleration DOUBLE PRECISION,
    growth_g_yt DOUBLE PRECISION,
    burst_z_yt DOUBLE PRECISION,
    engagement_e_yt DOUBLE PRECISION,
    breadth_b_yt DOUBLE PRECISION,
    platform_confirmation_p DOUBLE PRECISION,
    youtube_evidence_confidence DOUBLE PRECISION,
    gate_pass INTEGER NOT NULL,
    lifecycle_state TEXT NOT NULL,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_trend_topic_ts ON metrics_trend (topic_id, timestamp);

-- Calculated Gap Time-Series
CREATE TABLE IF NOT EXISTS metrics_gap (
    id SERIAL PRIMARY KEY,
    topic_id TEXT REFERENCES topics(id),
    batch_id TEXT REFERENCES raw_batches(batch_id),
    timestamp TEXT NOT NULL,
    gap_score DOUBLE PRECISION NOT NULL,
    demand_norm DOUBLE PRECISION NOT NULL,
    supply_norm DOUBLE PRECISION NOT NULL,
    creator_authority DOUBLE PRECISION NOT NULL,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_gap_topic_ts ON metrics_gap (topic_id, timestamp);

-- Creator Baseline Cache
CREATE TABLE IF NOT EXISTS creator_baselines (
    channel_id TEXT PRIMARY KEY,
    creator_rate DOUBLE PRECISION NOT NULL,
    sample_count INTEGER NOT NULL,
    details TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_creator_baselines_expires ON creator_baselines (expires_at);

-- Shared Atomic Quota Ledger
CREATE TABLE IF NOT EXISTS quota_ledger (
    date_pacific TEXT NOT NULL,
    profile TEXT NOT NULL,
    search_calls_used INTEGER DEFAULT 0,
    read_units_used INTEGER DEFAULT 0,
    search_budget INTEGER DEFAULT 100,
    read_budget INTEGER DEFAULT 10000,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (date_pacific, profile)
);
"""


@dataclass(frozen=True)
class TopicRow:
    """Structured representation of a topic row from the database."""

    id: str
    canonical_name: str
    query: str
    category_id: str | None
    aliases: tuple[str, ...]
    status: str
    lifecycle_state: str
    lifecycle_reason: str
    cadence: str
    priority: float
    next_discovery_at: str
    next_tracking_at: str
    locked_by: str | None
    locked_until: str | None
    nlp_model_version: str | None
    classification_confidence: float | None
    created_at: str
    updated_at: str


class DatabaseStorageAdapter:
    """Database storage adapter for relational persistence, task leasing, and metrics.

    Manages tables for categories, topics, raw bundles, metric snapshots, creator baselines,
    and the atomic quota ledger.
    """

    def __init__(self, db_path: str | Path | None = None, timeout: float = 30.0) -> None:
        conninfo = str(db_path) if db_path is not None else DB_PATH
        if db_path is not None and not str(db_path).startswith(("postgresql://", "postgres://", "host=", "dbname=")):
            conninfo = DB_PATH
        if not conninfo:
            raise ValueError("DB_PATH is not set. Please set POSTGRES_* environment variables.")
        self.db_path = conninfo
        self.timeout = timeout
        self._init_db()

    def _get_connection(self) -> psycopg.Connection:
        """Create a connection."""
        conn = psycopg.connect(conninfo=self.db_path, connect_timeout=self.timeout)
        conn.row_factory = dict_row
        return conn

    @contextmanager
    def transaction(self, immediate: bool = True) -> Iterator[psycopg.Connection]:
        """Execute operations within an atomic PostgreSQL transaction."""
        conn = self._get_connection()
        try:
            with conn.transaction():
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database schema."""
        conn = self._get_connection()
        try:
            conn.execute(POSTGRES_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def clean_tables(self) -> None:
        """Truncate all tables to reset state for testing or maintenance."""
        with self.transaction() as conn:
            conn.execute(
                """
                TRUNCATE TABLE metrics_gap, metrics_trend, raw_batches, topics, categories, creator_baselines, quota_ledger
                RESTART IDENTITY CASCADE;
                """
            )

    # -------------------------------------------------------------------------
    # Category Operations
    # -------------------------------------------------------------------------

    def seed_category(
        self,
        category_id: str,
        name: str,
        status: str = "ACTIVE",
    ) -> None:
        """Insert or update a top-level category."""
        now = iso(now_utc())
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO categories (id, name, status, aggregate_momentum, breakout_count, created_at, updated_at)
                VALUES (%s, %s, %s, 0.0, 0, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (category_id, name, status, now, now),
            )

    def get_category(self, category_id: str) -> dict[str, Any] | None:
        """Fetch category details by ID."""
        with self.transaction(immediate=False) as conn:
            cursor = conn.execute("SELECT * FROM categories WHERE id = %s", (category_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_categories(self, status: str | None = None) -> list[dict[str, Any]]:
        """List categories with optional status filter."""
        with self.transaction(immediate=False) as conn:
            if status:
                cursor = conn.execute(
                    "SELECT * FROM categories WHERE status = %s ORDER BY name ASC", (status,)
                )
            else:
                cursor = conn.execute("SELECT * FROM categories ORDER BY name ASC")
            return [dict(r) for r in cursor.fetchall()]

    def update_category_stats(
        self,
        category_id: str,
        aggregate_momentum: float,
        breakout_count: int,
    ) -> None:
        """Update rollup metrics on a category."""
        now = iso(now_utc())
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE categories
                SET aggregate_momentum = %s,
                    breakout_count = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (aggregate_momentum, breakout_count, now, category_id),
            )

    # -------------------------------------------------------------------------
    # Topic Operations & Atomic Leasing
    # -------------------------------------------------------------------------

    def register_topic(
        self,
        topic_id: str,
        canonical_name: str,
        query: str,
        category_id: str | None = None,
        aliases: tuple[str, ...] | list[str] | None = None,
        status: str = "ACTIVE",
        lifecycle_state: str = "WATCHING",
        lifecycle_reason: str = "Initial state",
        cadence: str = "REGULAR",
        priority: float = 1.0,
        nlp_model_version: str | None = None,
        classification_confidence: float | None = None,
    ) -> None:
        """Register or update a topic in the state machine."""
        now = iso(now_utc())
        aliases_json = json.dumps(list(aliases or []))
        with self.transaction() as conn:
            if category_id:
                cat = conn.execute("SELECT id FROM categories WHERE id = %s", (category_id,)).fetchone()
                if not cat:
                    category_id = None
            conn.execute(
                """
                INSERT INTO topics (
                    id, category_id, canonical_name, query, aliases,
                    nlp_model_version, classification_confidence,
                    status, lifecycle_state, lifecycle_reason, cadence, priority,
                    next_discovery_at, next_tracking_at, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                    category_id = COALESCE(excluded.category_id, topics.category_id),
                    canonical_name = excluded.canonical_name,
                    query = excluded.query,
                    aliases = excluded.aliases,
                    nlp_model_version = COALESCE(excluded.nlp_model_version, topics.nlp_model_version),
                    classification_confidence = COALESCE(excluded.classification_confidence, topics.classification_confidence),
                    status = excluded.status,
                    cadence = excluded.cadence,
                    priority = excluded.priority,
                    updated_at = excluded.updated_at
                """,
                (
                    topic_id,
                    category_id,
                    canonical_name,
                    query,
                    aliases_json,
                    nlp_model_version,
                    classification_confidence,
                    status,
                    lifecycle_state,
                    lifecycle_reason,
                    cadence,
                    priority,
                    now,
                    now,
                    now,
                    now,
                ),
            )

    def get_topic(self, topic_id: str) -> TopicRow | None:
        """Retrieve topic state by ID."""
        with self.transaction(immediate=False) as conn:
            cursor = conn.execute("SELECT * FROM topics WHERE id = %s", (topic_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_topic(dict(row))

    def list_topics(
        self,
        category_id: str | None = None,
        status: str | None = None,
        limit: int = 1000,
    ) -> list[TopicRow]:
        """List topics with optional category and status filtering."""
        with self.transaction(immediate=False) as conn:
            query = "SELECT * FROM topics WHERE 1=1"
            params: list[Any] = []
            if category_id:
                query += " AND category_id = %s"
                params.append(category_id)
            if status:
                query += " AND status = %s"
                params.append(status)
            query += " ORDER BY priority DESC, next_discovery_at ASC LIMIT %s"
            params.append(limit)
            cursor = conn.execute(query, params)
            return [self._row_to_topic(dict(r)) for r in cursor.fetchall()]

    def claim_due_topic(
        self,
        worker_id: str,
        lease_duration_seconds: int = 300,
    ) -> TopicRow | None:
        """Atomically claim the highest-priority due topic for execution.

        Leases a topic where:
        - Topic status is 'ACTIVE'
        - Cadence is not 'STOPPED'
        - Category is ACTIVE (or unassigned)
        - next_discovery_at <= now OR next_tracking_at <= now
        - locked_until is NULL or expired (< now)
        """
        now = now_utc()
        now_str = iso(now)
        lease_expiry = iso(now + timedelta(seconds=lease_duration_seconds))

        with self.transaction(immediate=True) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT t.id
                FROM topics t
                LEFT JOIN categories c ON t.category_id = c.id
                WHERE t.status = 'ACTIVE'
                  AND t.cadence != 'STOPPED'
                  AND (c.status = 'ACTIVE' OR t.category_id IS NULL)
                  AND (t.next_discovery_at <= %s OR t.next_tracking_at <= %s)
                  AND (t.locked_until IS NULL OR t.locked_until < %s)
                ORDER BY t.priority DESC, t.next_discovery_at ASC
                LIMIT 1
                FOR UPDATE OF t SKIP LOCKED
                """,
                (now_str, now_str, now_str),
            )
            candidate = cursor.fetchone()
            if not candidate:
                return None

            topic_id = candidate["id"]
            cursor.execute(
                """
                UPDATE topics
                SET locked_by = %s,
                    locked_until = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (worker_id, lease_expiry, now_str, topic_id),
            )
            cursor.execute("SELECT * FROM topics WHERE id = %s", (topic_id,))
            updated_row = dict(cursor.fetchone())
            return self._row_to_topic(updated_row)

    def release_topic(
        self,
        topic_id: str,
        worker_id: str,
        next_discovery_at: datetime | str | None = None,
        next_tracking_at: datetime | str | None = None,
        lifecycle_state: str | None = None,
        lifecycle_reason: str | None = None,
        cadence: str | None = None,
    ) -> None:
        """Release a topic lease and schedule next discovery/tracking times."""
        now = iso(now_utc())
        updates = ["locked_by = NULL", "locked_until = NULL", "updated_at = %s"]
        params: list[Any] = [now]

        if next_discovery_at is not None:
            updates.append("next_discovery_at = %s")
            params.append(
                iso(utc(next_discovery_at)) if isinstance(next_discovery_at, datetime) else next_discovery_at
            )

        if next_tracking_at is not None:
            updates.append("next_tracking_at = %s")
            params.append(
                iso(utc(next_tracking_at)) if isinstance(next_tracking_at, datetime) else next_tracking_at
            )

        if lifecycle_state is not None:
            updates.append("lifecycle_state = %s")
            params.append(lifecycle_state)

        if lifecycle_reason is not None:
            updates.append("lifecycle_reason = %s")
            params.append(lifecycle_reason)

        if cadence is not None:
            updates.append("cadence = %s")
            params.append(cadence)

        params.extend([topic_id, worker_id])
        sql = f"UPDATE topics SET {', '.join(updates)} WHERE id = %s AND (locked_by = %s OR locked_until < %s)"
        params.insert(-1, now)  # allow force-release if expired

        with self.transaction() as conn:
            conn.execute(sql, params)

    def update_topic_classification(
        self,
        topic_id: str,
        category_id: str,
        confidence: float,
        model_version: str,
        candidate_aliases: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        """Update topic category after NLP classification and promote to ACTIVE."""
        now = iso(now_utc())
        with self.transaction() as conn:
            cursor = conn.execute("SELECT aliases FROM topics WHERE id = %s", (topic_id,))
            row = cursor.fetchone()
            current_aliases: list[str] = json.loads(row["aliases"]) if row and row["aliases"] else []
            if candidate_aliases:
                for a in candidate_aliases:
                    if a not in current_aliases:
                        current_aliases.append(a)

            conn.execute(
                """
                UPDATE topics
                SET category_id = %s,
                    classification_confidence = %s,
                    nlp_model_version = %s,
                    aliases = %s,
                    status = 'ACTIVE',
                    updated_at = %s
                WHERE id = %s
                """,
                (
                    category_id,
                    confidence,
                    model_version,
                    json.dumps(current_aliases),
                    now,
                    topic_id,
                ),
            )

    @staticmethod
    def _row_to_topic(row: dict[str, Any]) -> TopicRow:
        raw_aliases = row.get("aliases") or "[]"
        if isinstance(raw_aliases, str):
            try:
                aliases = tuple(json.loads(raw_aliases))
            except Exception:
                aliases = ()
        else:
            aliases = tuple(raw_aliases)

        return TopicRow(
            id=str(row["id"]),
            canonical_name=str(row["canonical_name"]),
            query=str(row["query"]),
            category_id=row["category_id"],
            aliases=aliases,
            status=str(row["status"]),
            lifecycle_state=str(row["lifecycle_state"]),
            lifecycle_reason=str(row.get("lifecycle_reason", "")),
            cadence=str(row["cadence"]),
            priority=float(row["priority"]),
            next_discovery_at=str(row["next_discovery_at"]),
            next_tracking_at=str(row["next_tracking_at"]),
            locked_by=row["locked_by"],
            locked_until=row["locked_until"],
            nlp_model_version=row.get("nlp_model_version"),
            classification_confidence=row.get("classification_confidence"),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    # -------------------------------------------------------------------------
    # Immutable Raw Batch Operations (100% Replay Fidelity)
    # -------------------------------------------------------------------------

    def save_bundle(
        self,
        bundle: CollectionBundle,
        operation: str = "discovery",
        status: str = "COMPLETE",
    ) -> None:
        """Persist an immutable CollectionBundle verbatim to raw_batches."""
        topic_id = bundle.topic.topic_id
        batch_id = bundle.metadata.batch_id
        window_from = bundle.metadata.effective_from
        window_to = bundle.metadata.effective_to
        now = iso(now_utc())

        payload_json = json.dumps(encode(bundle), separators=(",", ":"), ensure_ascii=False)
        telemetry_json = json.dumps([encode(t) for t in bundle.telemetry], separators=(",", ":"))

        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO raw_batches (
                    batch_id, topic_id, operation, window_from, window_to,
                    status, payload, telemetry, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(batch_id) DO NOTHING
                """,
                (
                    batch_id,
                    topic_id,
                    operation,
                    window_from,
                    window_to,
                    status,
                    payload_json,
                    telemetry_json,
                    now,
                ),
            )

    def get_bundle(self, batch_id: str) -> CollectionBundle | None:
        """Retrieve and deserialize a CollectionBundle by batch_id for exact replay."""
        with self.transaction(immediate=False) as conn:
            cursor = conn.execute("SELECT payload FROM raw_batches WHERE batch_id = %s", (batch_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = json.loads(row["payload"])
            return CollectionBundle.from_dict(data)

    def list_bundles(self, topic_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """List raw batch metadata for a topic."""
        with self.transaction(immediate=False) as conn:
            cursor = conn.execute(
                """
                SELECT batch_id, topic_id, operation, window_from, window_to, status, created_at
                FROM raw_batches
                WHERE topic_id = %s
                ORDER BY window_to DESC
                LIMIT %s
                """,
                (topic_id, limit),
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_raw_batches(self, topic_id: str, limit: int = 100) -> list[CollectionBundle]:
        """Retrieve and deserialize CollectionBundles for a topic in chronological order."""
        with self.transaction(immediate=False) as conn:
            cursor = conn.execute(
                """
                SELECT payload FROM raw_batches
                WHERE topic_id = %s
                ORDER BY window_to ASC, created_at ASC
                LIMIT %s
                """,
                (topic_id, limit),
            )
            rows = cursor.fetchall()
            return [
                CollectionBundle.from_dict(
                    r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
                )
                for r in rows
            ]

    # -------------------------------------------------------------------------
    # Trend & Gap Time-Series Metric Operations
    # -------------------------------------------------------------------------

    def save_trend_metric(
        self,
        topic_id: str,
        batch_id: str,
        timestamp: datetime | str,
        trend_row: dict[str, Any],
    ) -> int:
        """Persist a calculated Trend Engine step row."""
        ts_str = iso(utc(timestamp)) if isinstance(timestamp, datetime) else timestamp
        now = iso(now_utc())

        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO metrics_trend (
                    topic_id, batch_id, timestamp,
                    youtube_trend_score, youtube_trend_score_public,
                    youtube_activity_count, youtube_incidence,
                    ewma, velocity, acceleration,
                    growth_g_yt, burst_z_yt, engagement_e_yt, breadth_b_yt,
                    platform_confirmation_p, youtube_evidence_confidence,
                    gate_pass, lifecycle_state, details, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    topic_id,
                    batch_id,
                    ts_str,
                    trend_row.get("youtube_trend_score"),
                    trend_row.get("youtube_trend_score_public"),
                    trend_row.get("youtube_activity_count"),
                    trend_row.get("youtube_incidence"),
                    trend_row.get("ewma"),
                    trend_row.get("velocity"),
                    trend_row.get("acceleration"),
                    trend_row.get("growth_G_YT"),
                    trend_row.get("burst_Z_YT"),
                    trend_row.get("engagement_E_YT"),
                    trend_row.get("breadth_B_YT"),
                    trend_row.get("platform_confirmation_P"),
                    trend_row.get("youtube_evidence_confidence"),
                    1 if trend_row.get("gate_pass") else 0,
                    trend_row.get("lifecycle_state", "WATCHING"),
                    json.dumps(encode(trend_row), separators=(",", ":")),
                    now,
                ),
            )
            row = cursor.fetchone()
            return row["id"] if row else 0

    def get_latest_trend(self, topic_id: str) -> dict[str, Any] | None:
        """Fetch latest calculated trend snapshot for a topic."""
        with self.transaction(immediate=False) as conn:
            cursor = conn.execute(
                """
                SELECT * FROM metrics_trend
                WHERE topic_id = %s
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """,
                (topic_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            res["details"] = json.loads(res["details"])
            return res

    def get_trend_history(self, topic_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch historical trend metrics for a topic."""
        with self.transaction(immediate=False) as conn:
            cursor = conn.execute(
                """
                SELECT * FROM metrics_trend
                WHERE topic_id = %s
                ORDER BY timestamp ASC
                LIMIT %s
                """,
                (topic_id, limit),
            )
            rows = []
            for r in cursor.fetchall():
                d = dict(r)
                d["details"] = json.loads(d["details"])
                rows.append(d)
            return rows

    def save_gap_metric(
        self,
        topic_id: str,
        batch_id: str,
        timestamp: datetime | str,
        gap_score: float,
        demand_norm: float,
        supply_norm: float,
        creator_authority: float,
        details: dict[str, Any],
    ) -> int:
        """Persist a calculated Gap Engine snapshot."""
        ts_str = iso(utc(timestamp)) if isinstance(timestamp, datetime) else timestamp
        now = iso(now_utc())

        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO metrics_gap (
                    topic_id, batch_id, timestamp,
                    gap_score, demand_norm, supply_norm, creator_authority,
                    details, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    topic_id,
                    batch_id,
                    ts_str,
                    gap_score,
                    demand_norm,
                    supply_norm,
                    creator_authority,
                    json.dumps(encode(details), separators=(",", ":")),
                    now,
                ),
            )
            row = cursor.fetchone()
            return row["id"] if row else 0

    def get_latest_gap(self, topic_id: str) -> dict[str, Any] | None:
        """Fetch latest calculated gap snapshot for a topic."""
        with self.transaction(immediate=False) as conn:
            cursor = conn.execute(
                """
                SELECT * FROM metrics_gap
                WHERE topic_id = %s
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """,
                (topic_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            res["details"] = json.loads(res["details"])
            return res

    def get_gap_history(self, topic_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch historical gap metrics for a topic."""
        with self.transaction(immediate=False) as conn:
            cursor = conn.execute(
                """
                SELECT * FROM metrics_gap
                WHERE topic_id = %s
                ORDER BY timestamp ASC
                LIMIT %s
                """,
                (topic_id, limit),
            )
            rows = []
            for r in cursor.fetchall():
                d = dict(r)
                d["details"] = json.loads(d["details"])
                rows.append(d)
            return rows

    # -------------------------------------------------------------------------
    # Creator Baselines Cache (Replaces creator_cache.json)
    # -------------------------------------------------------------------------

    def save_creator_baseline(
        self,
        channel_id: str,
        creator_rate: float,
        sample_count: int,
        details: dict[str, Any],
        ttl_hours: float = 12.0,
    ) -> None:
        """Save or refresh creator upload baseline statistics with TTL."""
        now = now_utc()
        now_str = iso(now)
        expires_str = iso(now + timedelta(hours=ttl_hours))

        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO creator_baselines (
                    channel_id, creator_rate, sample_count, details, updated_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT(channel_id) DO UPDATE SET
                    creator_rate = excluded.creator_rate,
                    sample_count = excluded.sample_count,
                    details = excluded.details,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at
                """,
                (
                    channel_id,
                    creator_rate,
                    sample_count,
                    json.dumps(encode(details)),
                    now_str,
                    expires_str,
                ),
            )

    def get_creator_baseline(self, channel_id: str) -> dict[str, Any] | None:
        """Retrieve valid, unexpired creator baseline statistics."""
        now_str = iso(now_utc())
        with self.transaction(immediate=False) as conn:
            cursor = conn.execute(
                """
                SELECT * FROM creator_baselines
                WHERE channel_id = %s AND expires_at > %s
                """,
                (channel_id, now_str),
            )
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            res["details"] = json.loads(res["details"])
            return res

    def prune_expired_creator_baselines(self) -> int:
        """Remove expired creator baseline cache rows."""
        now_str = iso(now_utc())
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM creator_baselines WHERE expires_at <= %s",
                (now_str,),
            )
            return cursor.rowcount

    # -------------------------------------------------------------------------
    # Shared Atomic Quota Ledger (Replaces local quota.sqlite)
    # -------------------------------------------------------------------------

    def get_quota_usage(self, profile: str, clock=now_utc) -> dict[str, Any]:
        """Query current recorded usage for this profile and day."""
        pacific_day = clock().astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
        with self.transaction(immediate=False) as conn:
            cursor = conn.execute(
                "SELECT * FROM quota_ledger WHERE profile = %s AND date_pacific = %s",
                (profile, pacific_day),
            )
            row = cursor.fetchone()
            if not row:
                return {
                    "profile": profile,
                    "date_pacific": pacific_day,
                    "search_calls_used": 0,
                    "read_units_used": 0,
                    "search_budget": 100,
                    "read_budget": 10000,
                    "search_remaining_percent": 100.0,
                }
            r = dict(row)
            budget = max(1, r["search_budget"])
            used = r["search_calls_used"]
            remaining = max(0, budget - used)
            r["search_remaining_percent"] = (remaining / budget) * 100.0
            return r

    def debit_quota(
        self,
        profile: str,
        endpoint: str,
        units: int = 1,
        search_budget: int = 100,
        read_budget: int = 10000,
        reserve_percent: float = 10.0,
        clock=now_utc,
    ) -> None:
        """Debit quota atomically before making an API call.

        Raises QuotaStopped if request would exceed available safety budget.
        """
        if endpoint not in {"search", "videos", "channels", "playlistItems"}:
            raise ValueError(f"Unknown endpoint quota cost: {endpoint}")

        pacific_day = clock().astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
        now_str = iso(clock())
        is_search = endpoint == "search"
        search_add = units if is_search else 0
        read_add = 0 if is_search else units

        with self.transaction(immediate=True) as conn:
            cursor = conn.execute(
                "SELECT search_calls_used, read_units_used FROM quota_ledger WHERE profile = %s AND date_pacific = %s",
                (profile, pacific_day),
            )
            row = cursor.fetchone()
            search_used = row["search_calls_used"] if row else 0
            read_used = row["read_units_used"] if row else 0

            if is_search:
                limit = search_budget * (1 - reserve_percent / 100)
                if search_used + units > limit:
                    raise QuotaStopped(f"Quota safety stop: search calls exceeded {limit:g}")
            else:
                limit = read_budget * (1 - reserve_percent / 100)
                if read_used + units > limit:
                    raise QuotaStopped(f"Quota safety stop: read units exceeded {limit:g}")

            conn.execute(
                """
                INSERT INTO quota_ledger (
                    date_pacific, profile, search_calls_used, read_units_used,
                    search_budget, read_budget, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(date_pacific, profile) DO UPDATE SET
                    search_calls_used = quota_ledger.search_calls_used + excluded.search_calls_used,
                    read_units_used = quota_ledger.read_units_used + excluded.read_units_used,
                    search_budget = excluded.search_budget,
                    read_budget = excluded.read_budget,
                    updated_at = excluded.updated_at
                """,
                (
                    pacific_day,
                    profile,
                    search_add,
                    read_add,
                    search_budget,
                    read_budget,
                    now_str,
                ),
            )

    def get_remaining_search_quota_percent(
        self,
        profile: str,
        search_budget: int = 100,
        clock=now_utc,
    ) -> float:
        """Return percentage of daily search quota remaining [0.0, 100.0]."""
        usage = self.get_quota_usage(profile, clock=clock)
        used = usage.get("search_calls_used", 0)
        remaining = max(0, search_budget - used)
        return (remaining / search_budget) * 100.0


class DatabaseQuotaManager:
    """PostgreSQL-backed shared atomic quota manager using the quota_ledger table."""

    def __init__(
        self,
        db: DatabaseStorageAdapter,
        profile: str,
        search_budget: int = 100,
        other_budget: int = 10000,
        reserve_percent: float = 10.0,
        clock=now_utc,
    ) -> None:
        if db is None:
            raise RuntimeError("DatabaseStorageAdapter connection required for DatabaseQuotaManager")
        self.db = db
        self.profile = profile
        self.search_budget = search_budget
        self.other_budget = other_budget
        self.reserve_percent = reserve_percent
        self.clock = clock
        self.limits = (search_budget, other_budget)
        self.reserve = reserve_percent

    def consume(self, endpoint: str) -> None:
        """Debit quota atomically from the PostgreSQL quota_ledger table."""
        self.db.debit_quota(
            self.profile,
            endpoint=endpoint,
            units=1,
            search_budget=self.search_budget,
            read_budget=self.other_budget,
            reserve_percent=self.reserve_percent,
            clock=self.clock,
        )

    def usage(self) -> dict[str, Any]:
        """Query current daily quota usage from PostgreSQL."""
        u = self.db.get_quota_usage(self.profile, clock=self.clock)
        return {
            "profile": self.profile,
            "search_calls_used": u.get("search_calls_used", 0),
            "other_units_estimated": u.get("read_units_used", 0),
            "search_calls_remaining": max(0, self.search_budget - u.get("search_calls_used", 0)),
            "other_units_remaining": max(0, self.other_budget - u.get("read_units_used", 0)),
            "search_budget": self.search_budget,
            "other_budget": self.other_budget,
            "reserve_percent": self.reserve_percent,
        }

    def next_reset(self) -> datetime:
        """Calculate next midnight Pacific time in UTC."""
        now = self.clock().astimezone(ZoneInfo("America/Los_Angeles"))
        midnight = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=ZoneInfo("America/Los_Angeles"))
        return midnight.astimezone(UTC)

    def seconds_until_reset(self) -> float:
        """Seconds remaining until Pacific midnight reset."""
        return max(0.0, (self.next_reset() - self.clock()).total_seconds())

    def estimate(self, config: ExperimentConfig) -> QuotaEstimate:
        """Calculate preflight quota estimate for an experiment configuration."""
        config.validate()
        duration_minutes = config.duration_hours * 60
        cycles = (
            1 if config.mode == Mode.HISTORICAL else math.ceil(duration_minutes / config.discovery_minutes)
        )
        if config.endless_mode:
            cycles = max(1, math.ceil(60 / config.discovery_minutes))
        requests = config.requests
        if config.mode == Mode.PRODUCT:
            requests = (max(requests, key=lambda r: r.page_size * r.max_pages),)
        search = cycles * (
            max(r.max_pages for r in config.requests)
            if config.mode == Mode.PRODUCT
            else sum(r.max_pages for r in requests)
        )
        other = cycles * sum(math.ceil(r.page_size * r.max_pages / 50) for r in requests)
        if config.mode in (Mode.GAP, Mode.COMBINED, Mode.PRODUCT):
            creators = sum(r.page_size * r.max_pages for r in requests)
            other += cycles * (
                math.ceil(creators / 50)
                + creators * config.baseline_pages
                + math.ceil(creators * config.baseline_max / 50)
            )
        if config.mode != Mode.GAP and config.mode != Mode.HISTORICAL:
            tracking_minutes = 60 if config.endless_mode else duration_minutes
            tracking_cycles = max(0, math.ceil(tracking_minutes / config.tracking_minutes) - 1)
            other += tracking_cycles * len(config.requests) * math.ceil(config.tracked_limit / 50)
        search *= config.max_retries + 1
        other *= config.max_retries + 1
        if config.endless_mode:
            search_per_hour, other_per_hour = float(search), float(other)
            search, other = math.ceil(search_per_hour * 24), math.ceil(other_per_hour * 24)
        used = self.usage()
        ratios = [
            (search + used["search_calls_used"]) / (self.limits[0] * (1 - self.reserve / 100)),
            (other + used["other_units_estimated"]) / (self.limits[1] * (1 - self.reserve / 100)),
        ]
        status = "WOULD EXCEED BUDGET" if max(ratios) > 1 else "WARNING" if max(ratios) > 0.8 else "SAFE"
        if config.endless_mode:
            return QuotaEstimate(
                search,
                other,
                status,
                "Endless local estimate per Pacific day (collection pauses at quota reset)",
                True,
                search_per_hour,
                other_per_hour,
                search,
                other,
            )
        return QuotaEstimate(search, other, status)
