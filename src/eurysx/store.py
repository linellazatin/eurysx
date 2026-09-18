"""Durable, metadata-only usage storage for Eurysx."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


SCHEMA_VERSION = 2
# 0 is a fresh store, 1 is the released schema (upgraded in place by creating the
# aggregate import table; no data moves). Anything else is a store this build cannot read.
SUPPORTED_SCHEMA_VERSIONS = (0, 1, 2)
# Pseudo-agent key for imported provider aggregates. It is never a harness, so the
# four --agent choices, detect_agents(), and PARSER_VERSIONS stay unchanged.
AGENT_AGGREGATE_IMPORTS = "anthropic-aggregate"


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
            if version not in SUPPORTED_SCHEMA_VERSIONS:
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
                CREATE TABLE IF NOT EXISTS aggregate_imports (
                    source_key TEXT NOT NULL REFERENCES sources(source_key) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    scope_label TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    model TEXT,
                    date TEXT NOT NULL,
                    bucket_end TEXT,
                    input_uncached_tokens INTEGER,
                    input_cached_tokens INTEGER,
                    cache_creation_tokens INTEGER,
                    output_tokens INTEGER,
                    cost_usd TEXT,
                    cost_type TEXT,
                    workspace_id TEXT,
                    service_tier TEXT,
                    context_window TEXT,
                    inference_geo TEXT,
                    speed TEXT,
                    complete INTEGER NOT NULL,
                    source_file TEXT NOT NULL,
                    source_mtime TEXT,
                    ingested_at TEXT NOT NULL,
                    parser_version TEXT,
                    PRIMARY KEY (source_key, ordinal)
                );
                CREATE INDEX IF NOT EXISTS aggregate_imports_date
                    ON aggregate_imports(date);
                CREATE INDEX IF NOT EXISTS aggregate_imports_model
                    ON aggregate_imports(model);
                CREATE INDEX IF NOT EXISTS aggregate_imports_source
                    ON aggregate_imports(source_key, date);
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

    def record_failure(self, source, agent, error):
        """Record a failed refresh without removing usable prior events."""
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO sources
                   (source_key, agent, fingerprint, parser_version, collected_at, last_error)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_key) DO UPDATE SET last_error=excluded.last_error""",
                (source.key, agent, source.fingerprint, source.parser_version,
                 datetime.now(timezone.utc).isoformat(), str(error)),
            )

    def replace_import_source(self, source_key, fingerprint, rows, parser_version="1"):
        """Atomically replace one declared import entry's aggregate rows.

        Imported rows live in their own table on purpose: events(), has_aggregate_events(),
        and distinct_agents() read the `events` table only, so an import cannot reach
        pricing, attribution, or any local cost lane even if a filter is forgotten.
        """
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
                (source_key, AGENT_AGGREGATE_IMPORTS, fingerprint, parser_version,
                 datetime.now(timezone.utc).isoformat()),
            )
            connection.execute("DELETE FROM aggregate_imports WHERE source_key = ?",
                               (source_key,))
            connection.executemany(
                """INSERT INTO aggregate_imports
                   (source_key, ordinal, scope_label, source_kind, model, date, bucket_end,
                    input_uncached_tokens, input_cached_tokens, cache_creation_tokens,
                    output_tokens, cost_usd, cost_type, workspace_id, service_tier,
                    context_window, inference_geo, speed, complete, source_file,
                    source_mtime, ingested_at, parser_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (source_key, ordinal, row.scope_label, row.source_kind, row.model,
                     row.date, row.bucket_end, row.input_uncached_tokens,
                     row.input_cached_tokens, row.cache_creation_tokens, row.output_tokens,
                     _decimal_text(row.cost_usd) if row.cost_usd is not None else None,
                     row.cost_type, row.workspace_id, row.service_tier, row.context_window,
                     row.inference_geo, row.speed, int(row.complete), row.source_file,
                     row.source_mtime, row.ingested_at, row.parser_version)
                    for ordinal, row in enumerate(rows)
                ],
            )

    def import_source_states(self):
        """Persisted state for declared import sources only."""
        return self.source_states([AGENT_AGGREGATE_IMPORTS])

    def imported_aggregates(self, start_date=None, end_date=None, models=None):
        """Stored aggregate rows; no join to events, by construction.

        A model filter keeps ungrouped rows (model IS NULL) visible, mirroring the
        Phase 3C unknown bucket rather than hiding reported cost.
        """
        conditions, parameters = [], []
        if start_date is not None:
            conditions.append("date >= ?")
            parameters.append(start_date.isoformat())
        if end_date is not None:
            conditions.append("date <= ?")
            parameters.append(end_date.isoformat())
        if models:
            conditions.append("(model IN (" + ", ".join("?" for _ in models) + ")"
                              " OR model IS NULL)")
            parameters.extend(models)
        query = "SELECT * FROM aggregate_imports"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY date, source_kind, model, cost_type"
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(query, parameters)]

    def source_states(self, agents=None):
        """Full persisted source state, optionally limited to agents."""
        agents = list(agents or [])
        query = ("SELECT source_key, agent, fingerprint, parser_version, collected_at, last_error "
                 "FROM sources")
        parameters = []
        if agents:
            query += " WHERE agent IN (" + ", ".join("?" for _ in agents) + ")"
            parameters.extend(agents)
        query += " ORDER BY agent, source_key"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def failing_sources(self, agents=None):
        return [row for row in self.source_states(agents) if row["last_error"] is not None]

    def all_sources(self):
        """Every registered source, for the read-only store-quality warnings."""
        return self.source_states()

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
