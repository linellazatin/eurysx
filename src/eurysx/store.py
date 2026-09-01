"""Durable, metadata-only usage storage for Eurysx."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


SCHEMA_VERSION = 1


def _decimal_text(value):
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return format(decimal, "f")


class UsageStore:
    """Replace normalized events for one collector source atomically."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        with self._connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise RuntimeError(
                    f"unsupported Eurysx store schema version: {version}"
                )
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS sources (
                    source_key TEXT PRIMARY KEY,
                    agent TEXT NOT NULL,
                    fingerprint TEXT,
                    parser_version TEXT,
                    collected_at TEXT NOT NULL,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    source_key TEXT NOT NULL REFERENCES sources(source_key) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    agent TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    session_id TEXT,
                    project_id TEXT,
                    model_id TEXT NOT NULL,
                    provider TEXT,
                    observed_provider TEXT,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cache_read_tokens INTEGER NOT NULL,
                    cache_write_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    model_requests INTEGER NOT NULL,
                    model_turns INTEGER NOT NULL,
                    model_tool_calls INTEGER NOT NULL,
                    recorded_cost_usd TEXT,
                    PRIMARY KEY (source_key, ordinal)
                );
                CREATE INDEX IF NOT EXISTS events_agent_timestamp
                    ON events(agent, timestamp);
            """)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @staticmethod
    def _event_type(entry):
        if getattr(entry, "is_aggregated", False):
            return "aggregate_usage"
        if getattr(entry, "is_metric_only", False):
            return "metric"
        return "usage"

    def replace_source(self, source_key, agent, fingerprint, entries,
                       parser_version="1"):
        """Atomically replace one source after successful collection."""
        collected_at = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO sources
                   (source_key, agent, fingerprint, parser_version, collected_at, last_error)
                   VALUES (?, ?, ?, ?, ?, NULL)
                   ON CONFLICT(source_key) DO UPDATE SET
                       agent=excluded.agent, fingerprint=excluded.fingerprint,
                       parser_version=excluded.parser_version,
                       collected_at=excluded.collected_at, last_error=NULL""",
                (source_key, agent, fingerprint, parser_version, collected_at),
            )
            connection.execute("DELETE FROM events WHERE source_key = ?", (source_key,))
            rows = []
            for ordinal, entry in enumerate(entries):
                recorded_cost = (
                    _decimal_text(entry.cost)
                    if getattr(entry, "cost_status", "unknown") == "recorded"
                    else None
                )
                rows.append((
                    source_key, ordinal, entry.agent, self._event_type(entry), entry.timestamp,
                    getattr(entry, "session_id", None), getattr(entry, "project_id", None),
                    entry.model_id, getattr(entry, "provider", None),
                    getattr(entry, "observed_provider", None), entry.input_tokens,
                    entry.output_tokens, entry.cache_read_tokens, entry.cache_write_tokens,
                    entry.total_tokens, entry.model_requests, entry.model_turns,
                    entry.model_tool_calls, recorded_cost,
                ))
            connection.executemany(
                """INSERT INTO events VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )

    def record_failure(self, source_key, error):
        """Record a failed refresh without removing usable prior events."""
        with self._connection() as connection:
            connection.execute(
                "UPDATE sources SET last_error = ? WHERE source_key = ?",
                (str(error), source_key),
            )

    def events(self, agents=None):
        agents = list(agents or [])
        query = "SELECT * FROM events"
        parameters = []
        if agents:
            query += " WHERE agent IN (" + ", ".join("?" for _ in agents) + ")"
            parameters.extend(agents)
        query += " ORDER BY timestamp, source_key, ordinal"
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(query, parameters)]

    def source_fingerprint(self, source_key):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT fingerprint FROM sources WHERE source_key = ?", (source_key,)
            ).fetchone()
        return row["fingerprint"] if row else None
