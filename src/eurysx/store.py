"""Durable, metadata-only usage storage for Eurysx."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
                CREATE INDEX IF NOT EXISTS events_provider ON events(provider);
                CREATE INDEX IF NOT EXISTS events_model_id ON events(model_id);
                CREATE INDEX IF NOT EXISTS events_project_id ON events(project_id);
                CREATE INDEX IF NOT EXISTS events_session_id ON events(session_id);
            """)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            # Idempotent purge of Phase 2 per-agent bulk rows (source_key 'collector:<agent>').
            connection.execute("DELETE FROM sources WHERE source_key LIKE 'collector:%'")

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

    def failing_sources(self):
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT source_key, last_error FROM sources"
                " WHERE last_error IS NOT NULL ORDER BY source_key"
            ).fetchall()
        return [dict(row) for row in rows]

    def events(self, agents=None, start_date=None, end_date=None,
               models=None, providers=None):
        """Filtered events; range mode mirrors filter_by_date_range semantics.

        models/providers filter with additive WHERE conditions; providers
        reads NULL provider rows as 'unknown', mirroring route-breakdown
        labels. Rows outside any ISO filter on date-only rows are
        unreachable: the claude-code stats-cache rows are the only date-only
        rows and they are aggregate_usage, excluded by the event_type guard
        in range mode.
        """
        agents = list(agents or [])
        models = list(models or [])
        providers = list(providers or [])
        conditions, parameters = [], []
        if agents:
            conditions.append("agent IN (" + ", ".join("?" for _ in agents) + ")")
            parameters.extend(agents)
        if models:
            conditions.append("model_id IN (" + ", ".join("?" for _ in models) + ")")
            parameters.extend(models)
        if providers:
            conditions.append(
                "COALESCE(provider, 'unknown') IN ("
                + ", ".join("?" for _ in providers) + ")"
            )
            parameters.extend(providers)
        if start_date is not None:
            conditions.append("event_type != 'aggregate_usage'")
            conditions.append("timestamp >= ? AND timestamp < ?")
            parameters.extend([
                start_date.isoformat(),
                (end_date + timedelta(days=1)).isoformat(),
            ])
        query = "SELECT * FROM events"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY timestamp, source_key, ordinal"
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(query, parameters)]

    def has_aggregate_events(self, agents=None):
        """Cheap presence check for aggregate rows (claude-code stats cache)."""
        agents = list(agents or [])
        query = "SELECT 1 FROM events WHERE event_type = 'aggregate_usage'"
        parameters = []
        if agents:
            query += " AND agent IN (" + ", ".join("?" for _ in agents) + ")"
            parameters.extend(agents)
        query += " LIMIT 1"
        with self._connection() as connection:
            return connection.execute(query, parameters).fetchone() is not None

    def distinct_agents(self, agents=None):
        """Distinct agents present in the store, for the ranged report shape."""
        agents = list(agents or [])
        query = "SELECT DISTINCT agent FROM events"
        parameters = []
        if agents:
            query += " WHERE agent IN (" + ", ".join("?" for _ in agents) + ")"
            parameters.extend(agents)
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return sorted(row["agent"] for row in rows)

    def source_state(self, source_key):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT fingerprint, parser_version, last_error FROM sources WHERE source_key = ?",
                (source_key,),
            ).fetchone()
        return dict(row) if row else None
