import ast
import json
import io
import os
import sqlite3
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

import eurysx.cli as app
import eurysx.analysis
import eurysx.models as models
import eurysx.paths as paths_module
import eurysx.pricing as pricing_module
import eurysx.paths
import eurysx.pricing
import eurysx.render
from eurysx.collectors import claude_code, codex, opencode
from eurysx.collectors import pi as pi_collector
from eurysx.collectors.sources import Source, fingerprint_paths
from eurysx.store import UsageStore


FIXTURES = Path(__file__).parent / "fixtures"


class CollectorFixtureTests(unittest.TestCase):
    def test_phase_one_modules_are_importable(self):
        self.assertTrue(callable(claude_code.collect))
        self.assertTrue(callable(codex.collect))
        self.assertTrue(callable(opencode.collect))

    def test_pi_collector_accepts_an_explicit_home_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session_dir = root / ".pi" / "agent" / "sessions" / "project"
            session_dir.mkdir(parents=True)
            (session_dir / "session.jsonl").write_text(
                (FIXTURES / "pi" / "session.jsonl").read_text()
            )

            usages = pi_collector.collect(root)

        self.assertEqual(len(usages), 2)
        self.assertEqual(usages[0].session_id, "pi-session-1")

    def test_opencode_fixture_is_tracked(self):
        self.assertTrue((FIXTURES / "opencode" / "database.sql").is_file())

    def test_claude_fixture_normalizes_usage_and_transcript_metrics(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            claude_dir = root / ".claude"
            claude_dir.mkdir()
            (claude_dir / "stats-cache.json").write_text(
                (FIXTURES / "claude_code" / "stats-cache.json").read_text()
            )
            (claude_dir / "session.jsonl").write_text(
                (FIXTURES / "claude_code" / "session.jsonl").read_text()
            )

            usages = claude_code.collect(root)

        self.assertEqual(len(usages), 1)
        usage = usages[0]
        self.assertEqual(usage.model_id, "claude-sonnet-4")
        self.assertIsNone(usage.provider)
        self.assertEqual(usage.timestamp, "2026-08-01")
        self.assertEqual(
            (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens,
             usage.cache_write_tokens, usage.total_tokens),
            (10, 20, 30, 40, 100),
        )
        self.assertEqual(
            (usage.model_requests, usage.model_turns, usage.model_tool_calls),
            (1, 1, 1),
        )
        self.assertIsNone(usage.session_id)

    def test_codex_fixture_normalizes_usage_metrics_and_session_identity(self):
        usages = codex.CodexExtractor.extract_usage_from_session(
            FIXTURES / "codex" / "rollout-session.jsonl"
        )

        usage = next(item for item in usages if not item.is_metric_only)
        self.assertEqual(usage.model_id, "gpt-5.6")
        self.assertEqual(usage.provider, "openai")
        self.assertEqual(usage.observed_provider, "openai")
        self.assertEqual(usage.timestamp, "2026-08-01T12:00:00Z")
        self.assertEqual(
            (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens,
             usage.cache_write_tokens, usage.total_tokens),
            (10, 20, 30, 40, 100),
        )
        self.assertEqual(usage.session_id, "codex-session-1")
        self.assertEqual(usage.project_id, "/repo/project-a")
        self.assertEqual(sum(item.model_requests for item in usages), 1)
        self.assertEqual(sum(item.model_turns for item in usages), 1)
        self.assertEqual(sum(item.model_tool_calls for item in usages), 1)

    def test_pi_fixture_normalizes_usage_metrics_and_session_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session_dir = root / ".pi" / "agent" / "sessions" / "project"
            session_dir.mkdir(parents=True)
            (session_dir / "session.jsonl").write_text(
                (FIXTURES / "pi" / "session.jsonl").read_text()
            )

            usages = pi_collector.collect(root)

        usage = next(item for item in usages if not item.is_metric_only)
        self.assertEqual(usage.model_id, "gpt-5.6")
        self.assertEqual(usage.provider, "openai")
        self.assertEqual(usage.timestamp, "2026-08-01T13:00:00Z")
        self.assertEqual(usage.session_id, "pi-session-1")
        self.assertEqual(usage.project_id, "/repo/project-a")
        self.assertEqual(
            (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens,
             usage.cache_write_tokens, usage.total_tokens),
            (10, 20, 30, 40, 100),
        )
        self.assertEqual(sum(item.model_requests for item in usages), 1)
        self.assertEqual(sum(item.model_turns for item in usages), 1)
        self.assertEqual(sum(item.model_tool_calls for item in usages), 1)

    def test_pi_falls_back_to_legacy_per_event_session_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session_dir = root / ".pi" / "agent" / "sessions" / "project"
            session_dir.mkdir(parents=True)
            (session_dir / "session.jsonl").write_text(
                '{"id":"a","type":"message","timestamp":"2026-08-01T13:00:00Z",'
                '"sessionId":"legacy-1","message":{"role":"assistant",'
                '"model":"m","provider":"openai","usage":{"input":1,"output":1,'
                '"cacheRead":0,"cacheWrite":0,"totalTokens":2}}}\n'
            )

            usages = pi_collector.collect(root)

        self.assertEqual([usage.session_id for usage in usages], ["legacy-1"])

    def test_opencode_database_normalizes_usage_metrics_and_session_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_dir = root / ".local" / "share" / "opencode"
            db_dir.mkdir(parents=True)
            db_path = db_dir / "opencode.db"
            self._write_opencode_fixture(db_path)

            usages = opencode.collect(root)

        usage = next(item for item in usages if not item.is_metric_only)
        self.assertEqual(usage.model_id, "gpt-5.6")
        self.assertEqual(usage.provider, "openai")
        self.assertEqual(usage.timestamp, "2025-08-01T14:00:00+00:00")
        self.assertEqual(usage.session_id, "opencode-session-1")
        self.assertEqual(usage.project_id, "/repo/project-a")
        self.assertEqual(
            (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens,
             usage.cache_write_tokens, usage.total_tokens),
            (10, 20, 30, 40, 100),
        )
        self.assertEqual(sum(item.model_requests for item in usages), 1)
        self.assertEqual(sum(item.model_turns for item in usages), 1)
        self.assertEqual(sum(item.model_tool_calls for item in usages), 1)

    def test_opencode_turn_detection_does_not_depend_on_message_row_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_dir = root / ".local" / "share" / "opencode"
            db_dir.mkdir(parents=True)
            db_path = db_dir / "opencode.db"
            self._write_opencode_fixture(db_path, assistant_first=True)

            usages = opencode.collect(root)

        self.assertEqual(sum(item.model_turns for item in usages), 1)

    @staticmethod
    def _write_opencode_fixture(db_path, assistant_first=False):
        if not assistant_first:
            conn = sqlite3.connect(db_path)
            conn.executescript((FIXTURES / "opencode" / "database.sql").read_text())
            conn.close()
            return
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE session (
                id TEXT, time_created INTEGER, model TEXT, tokens_input INTEGER,
                tokens_output INTEGER, tokens_cache_read INTEGER,
                tokens_cache_write INTEGER, cost REAL
            );
            CREATE TABLE message (
                id TEXT, session_id TEXT, time_created INTEGER, data TEXT
            );
            CREATE TABLE part (session_id TEXT, time_created INTEGER, data TEXT);
        """)
        model = json.dumps({"id": "gpt-5.6", "providerID": "openai"})
        conn.execute(
            "INSERT INTO session VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("opencode-session-1", 1754056800000, model, 10, 20, 30, 40, 1.25),
        )
        messages = [
            ("user-1", "opencode-session-1", 1754056700000, json.dumps({"role": "user"})),
            ("assistant-1", "opencode-session-1", 1754056800000, json.dumps({
                "role": "assistant", "modelID": "gpt-5.6", "providerID": "openai",
                "parentID": "user-1",
            })),
        ]
        if assistant_first:
            messages.reverse()
        conn.executemany(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            messages,
        )
        conn.execute(
            "INSERT INTO part VALUES (?, ?, ?)",
            ("opencode-session-1", 1754056800000, json.dumps({"type": "tool"})),
        )
        conn.commit()
        conn.close()


class UsageStoreTests(unittest.TestCase):
    def test_replacing_a_source_is_idempotent_and_preserves_decimal_cost(self):
        with tempfile.TemporaryDirectory() as temp:
            store = UsageStore(Path(temp) / "data" / "eurysx.db")
            first = app.UsageEntry(
                agent="codex", model_id="gpt-5.6", timestamp="2026-09-01T00:00:00Z",
                input_tokens=1, output_tokens=2, cache_read_tokens=0, cache_write_tokens=0,
                total_tokens=3, cost=0.123456789, cost_breakdown={"total": 0.123456789},
                provider="openai", observed_provider="openai", cost_status="recorded",
                session_id="session-1",
            )
            store.replace_source("codex:/session-1", "codex", "fingerprint-1", [first])
            store.replace_source("codex:/session-1", "codex", "fingerprint-1", [first])

            events = store.events(["codex"])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["recorded_cost_usd"], "0.123456789")
        self.assertEqual(events[0]["session_id"], "session-1")

    def test_report_command_reads_store_without_calling_collectors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = UsageStore(root / "data" / "eurysx.db")
            entry = app.UsageEntry(
                agent="codex", model_id="gpt-5.6", timestamp="2026-09-01T00:00:00Z",
                input_tokens=1, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
                total_tokens=1, cost=0.0, cost_breakdown={}, session_id="session-1",
            )
            store.replace_source("codex:/session-1", "codex", "fingerprint-1", [entry])
            output = root / "report.json"
            with patch("sys.argv", ["eurysx", "report", "--agent", "codex", "--output", str(output)]), \
                    patch.object(app, "get_eurysx_data_dir", return_value=root / "data"), \
                    patch.object(app, "collect_sources", side_effect=AssertionError), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                app.main()
            report = json.loads(output.read_text())

        self.assertEqual(report["agents_analyzed"], ["codex"])

    def test_failed_source_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            store = UsageStore(Path(temp) / "data" / "eurysx.db")
            entry = app.UsageEntry(
                agent="pi", model_id="gpt-5.6", timestamp="2026-09-01T00:00:00Z",
                input_tokens=1, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
                total_tokens=1, cost=0.0, cost_breakdown={}, session_id="session-1",
            )
            store.replace_source("pi:/session-1", "pi", "fingerprint-1", [entry])
            source = Source("pi:/session-1", "fingerprint-2", "3", lambda: [])
            store.record_failure(source, "pi", "parse failed")

            events = store.events(["pi"])

        self.assertEqual(len(events), 1)

    def test_first_refresh_failure_is_persisted_without_events(self):
        with tempfile.TemporaryDirectory() as temp:
            store = UsageStore(Path(temp) / "data" / "eurysx.db")
            source = Source("pi:first", "fingerprint-1", "3", lambda: [])
            store.record_failure(source, "pi", OSError("parse failed"))
            states = store.source_states(["pi"])

            self.assertEqual(store.events(["pi"]), [])
            self.assertEqual(len(states), 1)
            self.assertEqual(states[0]["last_error"], "parse failed")
            self.assertEqual(states[0]["fingerprint"], "fingerprint-1")
            self.assertEqual(states[0]["parser_version"], "3")

    def test_source_states_include_diagnostic_fields_and_filter_agents(self):
        with tempfile.TemporaryDirectory() as temp:
            store = UsageStore(Path(temp) / "data" / "eurysx.db")
            store.record_failure(Source("pi:one", "fp", "3", lambda: []), "pi", "failed")
            store.record_failure(Source("codex:one", "fp", "2", lambda: []), "codex", "failed")
            states = store.source_states(["pi"])

        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]["agent"], "pi")
        self.assertEqual(
            set(states[0]),
            {"source_key", "agent", "fingerprint", "parser_version", "collected_at", "last_error"},
        )

    def test_has_aggregate_events_presence_check(self):
        with tempfile.TemporaryDirectory() as temp:
            store = UsageStore(Path(temp) / "data" / "eurysx.db")
            aggregate = app.UsageEntry(
                agent="claude-code", model_id="claude-sonnet-4",
                timestamp="2026-08-01T00:00:00Z", input_tokens=1, output_tokens=0,
                cache_read_tokens=0, cache_write_tokens=0, total_tokens=1,
                cost=0.0, cost_breakdown={}, is_aggregated=True,
            )
            usage = app.UsageEntry(
                agent="pi", model_id="model", timestamp="2026-08-01T12:00:00Z",
                input_tokens=1, output_tokens=0, cache_read_tokens=0,
                cache_write_tokens=0, total_tokens=1, cost=0.0, cost_breakdown={},
            )
            store.replace_source("claude-code:one", "claude-code", "fp", [aggregate])
            store.replace_source("pi:one", "pi", "fp", [usage])

            self.assertTrue(store.has_aggregate_events())
            self.assertTrue(store.has_aggregate_events(["claude-code"]))
            self.assertFalse(store.has_aggregate_events(["pi"]))
            self.assertEqual(store.distinct_agents(), ["claude-code", "pi"])

    def test_sql_range_filter_matches_python_filter(self):
        with tempfile.TemporaryDirectory() as temp:
            store = UsageStore(Path(temp) / "data" / "eurysx.db")
            pi_rows = [
                app.UsageEntry(
                    agent="pi", model_id="model", timestamp="2026-08-01T12:00:00Z",
                    input_tokens=10, output_tokens=20, cache_read_tokens=30,
                    cache_write_tokens=40, total_tokens=100, cost=0.0,
                    cost_breakdown={}, provider="openai",
                ),
                app.UsageEntry(
                    agent="pi", model_id="model", timestamp="2026-08-03T23:59:59Z",
                    input_tokens=1, output_tokens=1, cache_read_tokens=0,
                    cache_write_tokens=0, total_tokens=2, cost=0.0,
                    cost_breakdown={}, provider="openai",
                ),
                app.UsageEntry(
                    agent="pi", model_id="model", timestamp="2026-08-04T00:00:00Z",
                    input_tokens=1, output_tokens=1, cache_read_tokens=0,
                    cache_write_tokens=0, total_tokens=2, cost=0.0,
                    cost_breakdown={}, provider="openai",
                ),
            ]
            aggregate = app.UsageEntry(
                agent="claude-code", model_id="claude-sonnet-4",
                timestamp="2026-08-01T00:00:00Z", input_tokens=1, output_tokens=0,
                cache_read_tokens=0, cache_write_tokens=0, total_tokens=1,
                cost=0.0, cost_breakdown={}, is_aggregated=True,
            )
            codex_rows = [
                app.UsageEntry(
                    agent="codex", model_id="model", timestamp="not-a-date",
                    input_tokens=1, output_tokens=0, cache_read_tokens=0,
                    cache_write_tokens=0, total_tokens=1, cost=0.0,
                    cost_breakdown={},
                ),
                app.UsageEntry(
                    agent="codex", model_id="model", timestamp="2026-07-31T23:59:59Z",
                    input_tokens=1, output_tokens=0, cache_read_tokens=0,
                    cache_write_tokens=0, total_tokens=1, cost=0.0,
                    cost_breakdown={},
                ),
            ]
            store.replace_source("pi:one", "pi", "fp", pi_rows)
            store.replace_source("claude-code:one", "claude-code", "fp", [aggregate])
            store.replace_source("codex:one", "codex", "fp", codex_rows)

            agents = ["pi", "claude-code", "codex"]
            start, end = date(2026, 8, 1), date(2026, 8, 3)

            ids = lambda records: sorted(
                (r["agent"], r["timestamp"]) for r in records
            )
            entry_ids = lambda entries: sorted(
                (entry.agent, entry.timestamp) for entry in entries
            )
            sql = ids(store.events(agents, start, end))
            python_reference = entry_ids(app.UsageAnalyzer.filter_by_date_range(
                [app._usage_from_store(r) for r in store.events(agents)],
                start, end, include_aggregated=False,
            ))
            self.assertEqual(sql, python_reference)
            self.assertTrue(sql)  # non-vacuous: at least the in-range pi rows
            self.assertNotIn(("claude-code", "2026-08-01T00:00:00Z"), sql)

            # All-time path keeps every row including aggregates and garbage.
            all_time = ids(store.events(agents))
            self.assertEqual(
                all_time,
                entry_ids(app.UsageAnalyzer.filter_by_date_range(
                    [app._usage_from_store(r) for r in store.events(agents)],
                    None, end, include_aggregated=True,
                )),
            )
            self.assertEqual(len(all_time), 6)

            # Single-agent filtering routes through the same WHERE clause.
            self.assertEqual(
                ids(store.events(["pi"], start, end)),
                [record for record in sql if record[0] == "pi"],
            )

    def test_grouping_dimensions_are_queryable(self):
        with tempfile.TemporaryDirectory() as temp:
            store = UsageStore(Path(temp) / "data" / "eurysx.db")
            rows = [
                app.UsageEntry(
                    agent="pi", model_id="model", timestamp="2026-08-01T10:00:00Z",
                    input_tokens=1, output_tokens=0, cache_read_tokens=0,
                    cache_write_tokens=0, total_tokens=1, cost=0.0,
                    cost_breakdown={}, session_id="s1", project_id="p1",
                ),
                app.UsageEntry(
                    agent="pi", model_id="model", timestamp="2026-08-01T12:00:00Z",
                    input_tokens=1, output_tokens=0, cache_read_tokens=0,
                    cache_write_tokens=0, total_tokens=1, cost=0.0,
                    cost_breakdown={}, session_id="s2", project_id="p1",
                ),
                app.UsageEntry(
                    agent="pi", model_id="model", timestamp="2026-08-02T12:00:00Z",
                    input_tokens=1, output_tokens=0, cache_read_tokens=0,
                    cache_write_tokens=0, total_tokens=1, cost=0.0,
                    cost_breakdown={}, session_id="s3",
                ),
            ]
            store.replace_source("pi:one", "pi", "fp", rows)
            with sqlite3.connect(store.path) as connection:
                project_buckets = dict(connection.execute(
                    "SELECT project_id, COUNT(*) FROM events GROUP BY project_id"
                ))
                session_buckets = dict(connection.execute(
                    "SELECT session_id, COUNT(*) FROM events GROUP BY session_id"
                ))
                day_buckets = dict(connection.execute(
                    "SELECT substr(timestamp, 1, 10), COUNT(*) FROM events"
                    " GROUP BY substr(timestamp, 1, 10)"
                ))

        self.assertEqual(project_buckets, {"p1": 2, None: 1})
        self.assertEqual(session_buckets, {"s1": 1, "s2": 1, "s3": 1})
        self.assertEqual(day_buckets, {"2026-08-01": 2, "2026-08-02": 1})

    def test_two_periods_from_one_query_path_are_disjoint(self):
        with tempfile.TemporaryDirectory() as temp:
            store = UsageStore(Path(temp) / "data" / "eurysx.db")
            rows = [
                app.UsageEntry(
                    agent="pi", model_id="model", timestamp=f"2026-{month:02d}-15T12:00:00Z",
                    input_tokens=1, output_tokens=0, cache_read_tokens=0,
                    cache_write_tokens=0, total_tokens=1, cost=0.0,
                    cost_breakdown={},
                )
                for month in (7, 8, 9)
            ]
            store.replace_source("pi:one", "pi", "fp", rows)
            july = store.events(["pi"], date(2026, 7, 1), date(2026, 7, 31))
            august = store.events(["pi"], date(2026, 8, 1), date(2026, 8, 31))
            all_months = store.events(["pi"])

        self.assertTrue(july)
        self.assertTrue(august)
        self.assertEqual(len(july) + len(august), len(all_months) - 1)
        self.assertEqual(
            {r["timestamp"] for r in july} & {r["timestamp"] for r in august},
            set(),
        )

    def test_events_dimension_indices_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "data" / "eurysx.db"
            UsageStore(path)
            connection = sqlite3.connect(path)
            names = {row[1] for row in connection.execute("PRAGMA index_list(events)")}
            connection.close()
            UsageStore(path)  # idempotent reopen on the same store
        self.assertTrue({
            "events_provider", "events_model_id",
            "events_project_id", "events_session_id",
        }.issubset(names))

    def test_events_model_and_provider_filters_sql(self):
        with tempfile.TemporaryDirectory() as temp:
            store = UsageStore(Path(temp) / "data" / "eurysx.db")
            pi_rows = [
                app.UsageEntry(
                    agent="pi", model_id="model-a", timestamp="2026-08-01T10:00:00Z",
                    input_tokens=1, output_tokens=0, cache_read_tokens=0,
                    cache_write_tokens=0, total_tokens=1, cost=0.0,
                    cost_breakdown={}, provider="openai",
                ),
                app.UsageEntry(
                    agent="pi", model_id="model-b", timestamp="2026-08-01T11:00:00Z",
                    input_tokens=1, output_tokens=0, cache_read_tokens=0,
                    cache_write_tokens=0, total_tokens=1, cost=0.0,
                    cost_breakdown={}, provider=None,
                ),
            ]
            codex_row = app.UsageEntry(
                agent="codex", model_id="model-a", timestamp="2026-08-01T12:00:00Z",
                input_tokens=1, output_tokens=0, cache_read_tokens=0,
                cache_write_tokens=0, total_tokens=1, cost=0.0,
                cost_breakdown={}, provider=None,
            )
            store.replace_source("pi:one", "pi", "fp", pi_rows)
            store.replace_source("codex:one", "codex", "fp", [codex_row])

            ids = lambda records: sorted((r["agent"], r["model_id"]) for r in records)
            self.assertEqual(ids(store.events(models=["model-a"])),
                             [("codex", "model-a"), ("pi", "model-a")])
            self.assertEqual(ids(store.events(providers=["openai"])),
                             [("pi", "model-a")])
            # NULL providers match 'unknown', mirroring route-breakdown labels.
            self.assertEqual(ids(store.events(providers=["unknown"])),
                             [("codex", "model-a"), ("pi", "model-b")])
            combined = store.events(["pi"], date(2026, 8, 1), date(2026, 8, 1),
                                    models=["model-a"], providers=["openai"])
            self.assertEqual(ids(combined), [("pi", "model-a")])
            self.assertEqual(store.events(models=["missing-model"]), [])


class IncrementalCollectionTests(unittest.TestCase):
    def _entry(self):
        return app.UsageEntry(
            agent="pi", model_id="model", timestamp="2026-09-01T00:00:00Z",
            input_tokens=1, output_tokens=0, cache_read_tokens=0,
            cache_write_tokens=0, total_tokens=1, cost=0.0,
            cost_breakdown={}, session_id="session-1", project_id="/repo/project-a",
        )

    def _collect(self, root, store, sources):
        with patch.object(app, "UsageStore", return_value=store), \
                patch.object(app, "collect_sources", side_effect=lambda agent, home=None: sources), \
                patch.object(app, "get_eurysx_data_dir", return_value=root / "data"), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            app.main(["collect", "--agent", "pi"])

    def test_unchanged_source_skips_reparse_until_fingerprint_or_parser_version_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = UsageStore(root / "data" / "eurysx.db")
            calls = []

            def parse():
                calls.append(1)
                return [self._entry()]

            self._collect(root, store, [Source("pi:one", "fp-a", "1", parse)])
            self._collect(root, store, [Source("pi:one", "fp-a", "1", parse)])
            self.assertEqual(len(calls), 1)
            self._collect(root, store, [Source("pi:one", "fp-b", "1", parse)])
            self.assertEqual(len(calls), 2)
            self._collect(root, store, [Source("pi:one", "fp-b", "2", parse)])
            self.assertEqual(len(calls), 3)
            events = store.events(["pi"])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["project_id"], "/repo/project-a")

    def _report(self, root, store):
        errors = io.StringIO()
        with patch.object(app, "UsageStore", return_value=store), \
                patch.object(app, "collect_sources", side_effect=AssertionError), \
                patch.object(app, "get_eurysx_data_dir", return_value=root / "data"), \
                redirect_stdout(io.StringIO()), redirect_stderr(errors):
            app.main(["report", "--agent", "pi"])
        return errors.getvalue()

    def test_failed_refresh_keeps_prior_events_and_records_the_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = UsageStore(root / "data" / "eurysx.db")
            store.replace_source("pi:one", "pi", "fp-a", [self._entry()])

            def boom():
                raise OSError("source vanished")

            self._collect(root, store, [Source("pi:one", "fp-b", "1", boom)])
            events = store.events(["pi"])
            state = store.source_state("pi:one")
            warning = self._report(root, store)

        self.assertEqual(len(events), 1)
        self.assertEqual(state["fingerprint"], "fp-a")
        self.assertIn("source vanished", state["last_error"])
        self.assertIn("pi has last-good data from 1 source(s) whose latest refresh failed", warning)
        self.assertNotIn("pi:one", warning)

    def test_report_warns_about_sources_still_on_an_older_parser(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = UsageStore(root / "data" / "eurysx.db")
            store.replace_source("pi:one", "pi", "fp-a", [self._entry()],
                                 parser_version="0")
            warning = self._report(root, store)
        self.assertIn(
            f"still on parser v0 (current v{app.PARSER_VERSIONS['pi']})", warning
        )
        self.assertIn("eurysx collect", warning)

    def test_report_warns_about_sources_that_no_longer_exist_on_disk(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = UsageStore(root / "data" / "eurysx.db")
            live = root / "session.jsonl"
            live.write_text("{}\n")
            current = app.PARSER_VERSIONS["pi"]
            store.replace_source(f"pi:{live}", "pi", "fp-a", [self._entry()],
                                 parser_version=current)
            store.replace_source(f"pi:{root / 'gone.jsonl'}", "pi", "fp-b",
                                 [self._entry()], parser_version=current)
            warning = self._report(root, store)
        self.assertIn("1 pi stored source(s) no longer exist on disk", warning)
        self.assertIn("retained as last-good data", warning)
        self.assertNotIn("still on parser", warning)

    def test_doctor_reports_source_health_without_parsing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = UsageStore(root / "data" / "eurysx.db")
            store.replace_source("pi:one", "pi", "old", [self._entry()], parser_version="0")
            store.record_failure(Source("pi:one", "new", "3", lambda: (_ for _ in ()).throw(AssertionError)), "pi", "failed")
            output = io.StringIO()
            with patch.object(app, "UsageStore", return_value=store), \
                    patch.object(app, "get_eurysx_data_dir", return_value=root / "data"), \
                    patch.object(app, "detect_agents", return_value=["pi"]), \
                    patch.object(app, "collect_sources", return_value=[Source("pi:one", "new", "3", lambda: (_ for _ in ()).throw(AssertionError))]), \
                    redirect_stdout(output), redirect_stderr(io.StringIO()):
                app.main(["doctor"])
        self.assertIn("DOCTOR", output.getvalue())
        self.assertIn("pi: detected", output.getvalue())
        self.assertIn("changed", output.getvalue())
        self.assertIn("parser v0", output.getvalue())
        self.assertIn("failed", output.getvalue())

    def test_fingerprint_is_stable_and_tracks_file_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.jsonl"
            path.write_text("{}\n")
            digest = fingerprint_paths([path])
            self.assertEqual(digest, fingerprint_paths([path]))
            os.utime(path, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
            self.assertNotEqual(digest, fingerprint_paths([path]))
            self.assertNotEqual(digest, fingerprint_paths([path.parent / "absent.jsonl"]))

    def test_pi_collector_enumerates_one_source_per_session_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            session_dir = root / ".pi" / "agent" / "sessions" / "project"
            session_dir.mkdir(parents=True)
            (session_dir / "session.jsonl").write_text(
                (FIXTURES / "pi" / "session.jsonl").read_text()
            )
            sources = pi_collector.enumerate_sources(root)
            usages = [entry for source in sources for entry in source.parse()]

        self.assertEqual(len(sources), 1)
        self.assertTrue(sources[0].key.startswith("pi:"))
        self.assertEqual(len(usages), 2)


class PricingTests(unittest.TestCase):
    def test_route_sources_use_primary_then_other_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            resolver = app.PricingResolver(root / "pricing.jsonc", root / "cache")
            resolver._add("amazon-bedrock", "model", {"input": 1, "output": 2}, "amazon-bedrock")
            resolver._add("amazon-bedrock", "model", {"input": 3, "output": 4}, "models-dev")

            result = resolver.resolve(
                "amazon-bedrock", "model", ["models-dev", "amazon-bedrock"]
            )

        self.assertEqual(result["source"], "models-dev")
        self.assertEqual(result["pricing"]["output"], 4)

    def test_resolved_prices_disclose_their_source_kind(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            resolver = app.PricingResolver(root / "pricing.jsonc", root / "cache")
            resolver._add("provider", "model", {"input": 1, "output": 2}, "models-dev")

            result = resolver.resolve("provider", "model")

        self.assertEqual(result["source_kind"], "catalog")

    def test_cache_directory_is_created_without_an_enabled_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = root / "cache"
            app.PricingResolver(root / "pricing.jsonc", cache)
            self.assertTrue(cache.exists())

    def test_inspection_mode_reads_cache_without_fetching_or_creating_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "pricing.jsonc"
            config.write_text('{"sources": {"models-dev": {"enabled": true, "url": "https://example.test"}}}')
            with patch.object(app.PricingResolver, "_fetch", side_effect=AssertionError):
                resolver = app.PricingResolver(config, root / "cache", inspect_only=True)
            self.assertFalse((root / "cache").exists())
            self.assertEqual(resolver.cache_status()[0]["status"], "missing")

    def test_cli_rejects_removed_pricing_file_override(self):
        with patch("sys.argv", ["eurysx", "--pricing-file", "x"]):
            with self.assertRaises(SystemExit):
                app.parse_args()

    def test_jsonc_override_wins_over_discovered_pricing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "pricing.jsonc"
            config.write_text("""
            {
              "sources": {},
              "overrides": {
                "litellm-proxy/sonnet": {"input": 2, "output": 10}
              }
            }
            """)
            resolver = app.PricingResolver(config, root / "cache")
            result = resolver.resolve("litellm-proxy", "sonnet")
            self.assertEqual(result["status"], "configured")
            self.assertEqual(result["pricing"]["output"], 10)

    def test_provider_alias_resolves_an_exact_configured_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "pricing.jsonc"
            config.write_text(json.dumps({
                "aliases": {"amazon-bedrock": {
                    "claude-sonnet-4-6": "global.anthropic.claude-sonnet-4-6",
                }},
                "overrides": {"amazon-bedrock/global.anthropic.claude-sonnet-4-6": {
                    "input": 3, "output": 15,
                }},
            }))
            resolver = app.PricingResolver(config, root / "cache")
            result = resolver.resolve("amazon-bedrock", "claude-sonnet-4-6", [])

        self.assertEqual(result["source"], "override")
        self.assertEqual(result["pricing"]["output"], 15)

    def test_unresolved_metered_routes_are_reported_by_provider_and_model(self):
        usage = app.UsageEntry(
            agent="codex", model_id="unpriced", timestamp="2026-08-01T00:00:00Z",
            input_tokens=10, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
            total_tokens=10, cost=0.0, cost_breakdown={}, provider="openai",
        )
        usage.billing_mode = "metered"
        usage.cost_status = "unknown"
        stats = app.UsageAnalyzer.analyze_agent(
            "codex", [usage], date(2026, 8, 1), date(2026, 8, 1), "1d"
        )
        self.assertEqual(stats.unresolved_routes[0]["provider"], "openai")
        self.assertEqual(stats.unresolved_routes[0]["model"], "unpriced")
        self.assertEqual(stats.unresolved_routes[0]["tokens"], 10)

    def test_unknown_provider_model_has_unknown_cost_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "pricing.jsonc"
            config.write_text(json.dumps({"sources": {}, "overrides": {}}))
            resolver = app.PricingResolver(config, root / "cache")
            resolver._add("provider", "model", {"input": 1, "output": 2}, "local")
            self.assertEqual(resolver.resolve("provider", "model")["status"], "cached")
            self.assertEqual(resolver.resolve("other", "missing")["status"], "unknown")

    def test_lower_priority_number_wins_for_same_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            resolver = app.PricingResolver(root / "pricing.jsonc", root / "cache")
            resolver._add("provider", "model", {"input": 9, "output": 9}, "later", priority=3)
            resolver._add("provider", "model", {"input": 1, "output": 1}, "earlier", priority=1)
            self.assertEqual(resolver.resolve("provider", "model")["source"], "earlier")

    def test_provider_qualified_pricing_does_not_match_another_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            resolver = app.PricingResolver(root / "pricing.jsonc", root / "cache")
            resolver._add("other-provider", "model", {"input": 1, "output": 1}, "local")

            self.assertEqual(resolver.resolve("target-provider", "model")["status"], "unknown")

    def test_pi_store_is_used_only_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pi_dir = root / ".pi" / "agent"
            pi_dir.mkdir(parents=True)
            (pi_dir / "models-store.json").write_text(json.dumps({
                "provider": {"models": [{
                    "id": "model",
                    "cost": {"input": 1, "output": 2},
                }]}
            }))
            config = root / "pricing.jsonc"
            config.write_text(json.dumps({"sources": {
                "pi-models-store": {"enabled": True, "priority": 2, "refreshDays": 15}
            }}))
            with patch.object(pricing_module.Path, "home", return_value=root):
                resolver = app.PricingResolver(config, root / "cache")
            result = resolver.resolve("provider", "model")
            self.assertEqual(result["source"], "pi-models-store")
            self.assertEqual(result["pricing"]["output"], 2)
            self.assertTrue((root / "cache" / "pricing-pi-models-store.json").exists())

    @patch("eurysx.pricing.subprocess.run")
    def test_aws_pricing_uses_configured_profile_and_region(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps({"PriceList": []})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "pricing.jsonc"
            config.write_text(json.dumps({"sources": {
                "amazon-bedrock": {"enabled": True, "profile": "p", "region": "r"}
            }}))
            app.PricingResolver(config, root / "cache")
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["aws", "pricing", "get-products", "--profile"])
        self.assertIn("p", command)
        self.assertIn("r", command)

    def test_stale_cache_is_used_when_refresh_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = root / "cache"
            cache.mkdir()
            cached = {
                "schema_version": 2, "source": "models-dev",
                "fetched_at": "2000-01-01T00:00:00",
                "models": {"provider/model": {"input": 1, "output": 2}},
            }
            (cache / "pricing-models-dev.json").write_text(json.dumps(cached))
            config = root / "pricing.jsonc"
            config.write_text(json.dumps({"sources": {
                "models-dev": {"enabled": True, "url": "http://invalid", "refreshDays": 1}
            }}))
            with patch.object(app.PricingResolver, "_fetch", side_effect=OSError("offline")):
                resolver = app.PricingResolver(config, cache)
            self.assertEqual(resolver.resolve("provider", "model")["status"], "cached")
            self.assertTrue(resolver.warnings)

    def test_malformed_pricing_configuration_becomes_a_diagnostic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "pricing.jsonc"
            config.write_text("{not valid json")
            resolver = app.PricingResolver(config, root / "cache")

        self.assertTrue(resolver.warnings)
        self.assertEqual(resolver.resolve("provider", "model")["status"], "unknown")

    def test_invalid_pricing_source_settings_use_safe_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "pricing.jsonc"
            config.write_text(json.dumps({"sources": {
                "pi-models-store": {
                    "enabled": True, "priority": "invalid", "refreshDays": "invalid",
                }
            }}))
            with patch.object(app.PricingResolver, "_fetch", return_value={}):
                resolver = app.PricingResolver(config, root / "cache")

        self.assertTrue(resolver.warnings)

    def test_pricing_application_keeps_resolved_source_kind(self):
        with tempfile.TemporaryDirectory() as temp:
            usage = app.UsageEntry(
                agent="codex", model_id="model", timestamp="2026-08-21T00:00:00",
                input_tokens=10, output_tokens=10, cache_read_tokens=0,
                cache_write_tokens=0, total_tokens=20, cost=0.0,
                cost_breakdown={}, provider="provider",
            )
            usage.billing_mode = "metered"
            resolver = app.PricingResolver(Path(temp) / "missing.jsonc", Path(temp) / "cache")
            resolver._add("provider", "model", {"input": 1, "output": 1}, "models-dev")
            app.apply_pricing([usage], resolver)

        self.assertEqual(usage.pricing_source, "models-dev")
        self.assertEqual(usage.pricing_source_kind, "catalog")

    def test_estimated_cost_is_known_in_presentation(self):
        self.assertEqual(
            eurysx.render._cost_status({"cost_status_counts": {"estimated": 1}}), "known"
        )

    def test_analysis_keeps_pricing_source_kinds(self):
        usage = app.UsageEntry(
            agent="codex", model_id="model", timestamp="2026-08-21T00:00:00",
            input_tokens=10, output_tokens=10, cache_read_tokens=0,
            cache_write_tokens=0, total_tokens=20, cost=0.02,
            cost_breakdown={"total": 0.02}, provider="provider",
            billing_mode="metered", cost_status="estimated",
            pricing_source="models-dev", pricing_source_kind="catalog",
        )

        stats = app.UsageAnalyzer.analyze_agent(
            "codex", [usage], date(2026, 8, 21), date(2026, 8, 21), "1d"
        )

        self.assertEqual(stats.pricing_source_kinds, {"models-dev": "catalog"})

    def test_recorded_cost_is_not_repriced(self):
        with tempfile.TemporaryDirectory() as temp:
            usage = app.UsageEntry(
                agent="pi", model_id="model", timestamp="2026-08-21T00:00:00",
                input_tokens=10, output_tokens=10, cache_read_tokens=0,
                cache_write_tokens=0, total_tokens=20, cost=7.5,
                cost_breakdown={"total": 7.5}, provider="provider",
                cost_status="recorded",
            )
            resolver = app.PricingResolver(Path(temp) / "missing.jsonc", Path(temp) / "cache")
            resolver._add("provider", "model", {"input": 1, "output": 1}, "local")
            app.apply_pricing([usage], resolver)
            self.assertEqual(usage.cost, 7.5)
            self.assertEqual(usage.cost_status, "recorded")


class CostCoverageTests(unittest.TestCase):
    @staticmethod
    def _usage(cost_status, cost, total_tokens, model="model", billing_mode="metered"):
        return app.UsageEntry(
            agent="pi", model_id=model, timestamp="2026-08-01T12:00:00Z",
            input_tokens=total_tokens, output_tokens=0, cache_read_tokens=0,
            cache_write_tokens=0, total_tokens=total_tokens, cost=cost,
            cost_breakdown={"total": cost} if cost_status != "unknown" else {},
            provider="provider", billing_mode=billing_mode, cost_status=cost_status,
        )

    def test_analysis_reports_known_cost_and_token_coverage(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi",
            [self._usage("recorded", 0.0, 100), self._usage("unknown", 0.0, 100)],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )

        self.assertEqual(stats.total_cost, 0.0)
        self.assertEqual(stats.known_cost, 0.0)
        self.assertEqual(stats.unknown_cost_tokens, 100)
        self.assertEqual(stats.priced_token_coverage, 0.5)

    def test_all_time_analysis_uses_observed_dates_for_cost_rates(self):
        first = self._usage("recorded", 1.0, 100)
        last = self._usage("recorded", 2.0, 100)
        last.timestamp = "2026-08-03T12:00:00Z"

        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [first, last], None, date(2026, 8, 3), "ALL TIME",
        )

        self.assertEqual(stats.total_cost, 3.0)
        self.assertEqual(stats.daily_cost, 1.0)

    @staticmethod
    def _terminal_report(stats):
        """Structured result a terminal-render test needs."""
        period = date(2026, 8, 1)
        return app.AnalysisReport(
            start_date=period, end_date=period, period_label="1d",
            agent_stats={"pi": stats},
            agent_displays={"pi": models.AgentDisplay(period, period, "1d")},
        )

    def test_terminal_labels_cost_as_known_when_pricing_is_incomplete(self):
        usages = [self._usage("configured", 1.5, 100), self._usage("unknown", 0.0, 100)]
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", usages,
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            app.print_single_agent_report(self._terminal_report(stats), "pi")

        self.assertIn("KNOWN COST", output.getvalue())
        self.assertIn("Metered token coverage:", output.getvalue())

    def test_zero_token_report_marks_pricing_coverage_not_applicable(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [self._usage("recorded", 0.0, 0)], date(2026, 8, 1), date(2026, 8, 1), "1d",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            app.print_single_agent_report(self._terminal_report(stats), "pi")

        self.assertIsNone(stats.priced_token_coverage)
        self.assertIn("Metered token coverage:", output.getvalue())
        self.assertIn("N/A", output.getvalue())

    def test_analysis_separates_metered_coverage_from_non_metered_usage(self):
        metered = self._usage("recorded", 1.0, 100)
        metered.billing_mode = "metered"
        metered.provider = "amazon-bedrock"
        subscription = self._usage("not_applicable", 0.0, 100)
        subscription.billing_mode = "subscription"
        subscription.provider = "openai"
        unknown = self._usage("unknown", 0.0, 100)
        unknown.billing_mode = "metered"
        unknown.provider = "amazon-bedrock"

        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [metered, subscription, unknown],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            app.print_single_agent_report(self._terminal_report(stats), "pi")

        self.assertEqual(stats.metered_tokens, 200)
        self.assertEqual(stats.non_metered_tokens, {"subscription": 100})
        self.assertEqual(stats.priced_token_coverage, 0.5)
        self.assertEqual(stats.route_breakdown["amazon-bedrock/model [metered]"]["tokens"], 200)
        self.assertIn("Metered token coverage:", output.getvalue())

    def test_route_cost_statuses_preserve_unknown_non_metered_and_partial_costs(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [
                self._usage("unknown", 0.0, 10, model="unknown-model"),
                self._usage("not_applicable", 0.0, 20, model="subscription-model", billing_mode="subscription"),
                self._usage("recorded", 0.0, 30, model="recorded-zero"),
                self._usage("configured", 1.5, 40, model="partial-model"),
                self._usage("unknown", 0.0, 50, model="partial-model"),
            ],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )

        self.assertEqual(
            stats.route_breakdown["provider/unknown-model [metered]"]["cost_status_counts"],
            {"unknown": 1},
        )
        self.assertEqual(
            stats.route_breakdown["provider/subscription-model [subscription]"]["cost_status_counts"],
            {"not_applicable": 1},
        )
        self.assertEqual(
            stats.route_breakdown["provider/recorded-zero [metered]"]["cost_status_counts"],
            {"recorded": 1},
        )
        self.assertEqual(
            stats.route_breakdown["provider/partial-model [metered]"]["cost_status_counts"],
            {"configured": 1, "unknown": 1},
        )

    def test_exports_and_terminal_label_unavailable_route_costs(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [
                self._usage("unknown", 0.0, 10, model="unknown-model"),
                self._usage("not_applicable", 0.0, 20, model="subscription-model", billing_mode="subscription"),
                self._usage("recorded", 0.0, 30, model="recorded-zero"),
                self._usage("configured", 1.5, 40, model="partial-model"),
                self._usage("unknown", 0.0, 50, model="partial-model"),
            ],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )
        report = self._terminal_report(stats)
        terminal = io.StringIO()
        with redirect_stdout(terminal):
            app.print_single_agent_report(report, "pi")

        self.assertIn("Provider", terminal.getvalue())
        self.assertIn("Cost status", terminal.getvalue())
        self.assertIn("N/A", terminal.getvalue())
        self.assertIn("partial", terminal.getvalue())
        csv_report = app.build_csv_report(report)
        self.assertIn(
            "pi,provider,provider,unknown-model,metered,10,N/A,1,0,0,0,unknown",
            csv_report,
        )
        self.assertIn(
            "pi,provider,provider,partial-model,metered,90,1.5,2,0,0,0,partial",
            csv_report,
        )
        self.assertIn(
            "| provider | provider | unknown-model | metered | 10 | N/A | unknown |",
            app.build_markdown_report(report),
        )

    def test_comparison_leaders_are_shared_by_non_csv_outputs(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [self._usage("recorded", 1.0, 10, model="small"),
                   self._usage("recorded", 2.0, 20, model="large")],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )
        report = self._terminal_report(stats)
        terminal = io.StringIO()
        with redirect_stdout(terminal):
            app.print_summary_comparison(report)

        leaders = {"agents": {"pi": {"provider": "provider", "model": "large", "tokens": 20}},
                   "combined": [{"provider": "provider", "model": "large", "tokens": 20},
                                {"provider": "provider", "model": "small", "tokens": 10}]}
        self.assertEqual(app.build_json_report(report)["comparison_summary"]["leaders"], leaders)
        self.assertIn("TOKEN LEADERS", terminal.getvalue())
        self.assertIn("provider", app.build_markdown_report(report))
        self.assertIn("TOKEN LEADERS", app.build_html_reports(report)["index.html"])

    def test_terminal_metric_blocks_align_value_columns(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [self._usage("recorded", 1.5, 10),
                   self._usage("not_applicable", 0.0, 20, billing_mode="subscription")],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            app.print_single_agent_report(self._terminal_report(stats), "pi")

        text = output.getvalue()
        projections = text.split("COST PROJECTIONS PER TIME PERIOD", 1)[1].split("TOKEN VOLUME", 1)[0]
        costs = text.split("COST ANALYSIS", 1)[1].split("Route breakdown:", 1)[0]
        projection_lines = [line for line in projections.splitlines() if line.startswith(("Daily", "Weekly", "Monthly", "Quarterly", "Yearly"))]
        cost_lines = [line for line in costs.splitlines() if line.startswith(("Known", "Unknown", "Metered", "Subscription"))]

        self.assertEqual(len({len(line.rstrip()) for line in projection_lines}), 1)
        self.assertEqual(len({len(line.rstrip()) for line in cost_lines}), 1)

    def test_html_report_renders_analysis_sections_and_unavailable_cost(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [
                self._usage("configured", 1.5, 40, model="priced-model"),
                self._usage("unknown", 0.0, 10, model="unknown-model"),
            ],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )
        stats.pricing_sources.add("models-dev")
        stats.pricing_fetched_at["models-dev"] = "2026-08-01T00:00:00Z"
        pages = app.build_html_reports(self._terminal_report(stats))
        html = "\n".join(pages.values())

        for section in (
            "COMPARISON SUMMARY", "DAILY ACTIVITY", "BREAKDOWN BY MODEL",
            "PRICING PROVENANCE", "BREAKDOWN BY PROJECT", "BREAKDOWN BY SESSION",
        ):
            self.assertIn(section, html)
        self.assertIn("priced-model", html)
        self.assertIn("unknown-model", html)
        self.assertIn("models-dev", html)
        self.assertIn("2026-08-01T00:00:00Z", html)
        self.assertIn("N/A", html)

    def test_html_report_bundle_has_summary_agent_page_and_navigation(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [self._usage("recorded", 1.5, 10)],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )

        pages = app.build_html_reports(self._terminal_report(stats))

        self.assertEqual(sorted(pages), ["index.html", "pi.html"])
        self.assertIn("COMBINED TOTAL", pages["index.html"])
        self.assertIn('href="pi.html"', pages["index.html"])
        self.assertIn('href="index.html"', pages["pi.html"])
        for section in (
            "TOTAL USAGE", "COST PROJECTIONS", "TOKEN VOLUME",
            "MODEL ACTIVITY VOLUME", "COST ANALYSIS", "PRICING PROVENANCE",
        ):
            self.assertIn(section, pages["pi.html"])

    def test_html_agent_sections_are_collapsible_and_large_tables_sortable(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [self._usage("recorded", 1.5, 10, model="model-b"),
                   self._usage("recorded", 1.0, 20, model="model-a")],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )

        page = app.build_html_reports(self._terminal_report(stats))["pi.html"]

        self.assertIn("<details open><summary>TOTAL USAGE</summary>", page)
        self.assertIn("<details><summary>BREAKDOWN BY MODEL", page)
        self.assertIn('<table class="sortable">', page)
        self.assertIn('aria-sort="none"', page)
        self.assertIn("addEventListener('click'", page)

    def test_html_summary_surfaces_the_leading_harness(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [self._usage("recorded", 1.5, 10)],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )

        summary = app.build_html_reports(self._terminal_report(stats))["index.html"]

        self.assertIn("At a glance", summary)
        self.assertIn("Most usage", summary)
        self.assertIn("PI CODING AGENT", summary)

    def test_html_breakdowns_show_counts_and_scroll_on_narrow_screens(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [self._usage("recorded", 1.5, 10, model="model-b"),
                   self._usage("recorded", 1.0, 20, model="model-a")],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )

        page = app.build_html_reports(self._terminal_report(stats))["pi.html"]

        self.assertIn("2 models", page)
        self.assertIn('<div class="table-scroll">', page)

    def test_html_overview_labels_unknown_cost_as_unavailable(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [self._usage("unknown", 0.0, 10)],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )

        summary = app.build_html_reports(self._terminal_report(stats))["index.html"]

        self.assertIn("N/A", summary)
        self.assertNotIn("$0.000000", summary)

    def test_daily_activity_labels_unknown_cost_as_unavailable(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [self._usage("unknown", 0.0, 10)],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            app.print_single_agent_report(self._terminal_report(stats), "pi")

        daily_section = output.getvalue().split("DAILY ACTIVITY", 1)[1].split("SUMMARY STATISTICS", 1)[0]
        self.assertIn("Cost status", daily_section)
        self.assertIn("N/A", daily_section)
        self.assertIn("unknown", daily_section)

    def test_unknown_route_tokens_do_not_reduce_metered_coverage(self):
        metered = self._usage("recorded", 1.0, 100)
        metered.billing_mode = "metered"
        unclassified = self._usage("unknown", 0.0, 500)
        unclassified.billing_mode = "unknown"

        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [metered, unclassified],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )

        self.assertEqual(stats.metered_tokens, 100)
        self.assertEqual(stats.unknown_cost_tokens, 0)
        self.assertEqual(stats.priced_token_coverage, 1.0)

    def test_billing_mode_filter_keeps_only_selected_modes(self):
        metered = self._usage("recorded", 1.0, 100)
        metered.billing_mode = "metered"
        subscription = self._usage("recorded", 1.0, 50)
        subscription.billing_mode = "subscription"

        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [metered, subscription],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
            billing_modes={"subscription"},
        )

        self.assertEqual(stats.usage_entries, 1)
        self.assertEqual(stats.total_tokens, 50)
        self.assertEqual(stats.metered_tokens, 0)
        self.assertEqual(stats.non_metered_tokens, {"subscription": 50})

    def test_billing_mode_filter_default_keeps_all_modes(self):
        metered = self._usage("recorded", 1.0, 100)
        metered.billing_mode = "metered"
        subscription = self._usage("recorded", 1.0, 50)
        subscription.billing_mode = "subscription"

        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [metered, subscription],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )

        self.assertEqual(stats.usage_entries, 2)
        self.assertEqual(stats.total_tokens, 150)


class GroupingDimensionTests(unittest.TestCase):
    """Phase 3C: per-project and per-session grouping in analysis and terminal."""

    @staticmethod
    def _usage(project_id=None, session_id=None, tokens=100, cost=1.0):
        return app.UsageEntry(
            agent="pi", model_id="model", timestamp="2026-08-01T12:00:00Z",
            input_tokens=tokens, output_tokens=0, cache_read_tokens=0,
            cache_write_tokens=0, total_tokens=tokens, cost=cost,
            cost_breakdown={"total": cost}, provider="openai",
            cost_status="recorded", project_id=project_id, session_id=session_id,
            model_requests=1, model_turns=1, model_tool_calls=0,
        )

    @staticmethod
    def _bucket(*buckets):
        return {
            "cache_read": 0, "cache_write": 0, "cost": sum(b[1] for b in buckets),
            "cost_status_counts": {"recorded": len(buckets)},
            "input": sum(b[0] for b in buckets), "model_requests": len(buckets),
            "model_tool_calls": 0, "model_turns": len(buckets), "output": 0,
        }

    @staticmethod
    def _report(stats):
        period = date(2026, 8, 1)
        return app.AnalysisReport(
            start_date=period, end_date=period, period_label="1d",
            agent_stats={"pi": stats},
            agent_displays={"pi": models.AgentDisplay(period, period, "1d")},
        )

    def test_project_and_session_breakdowns_group_by_attribute(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [
                self._usage(project_id="/repo/a", session_id="s1", tokens=10, cost=0.1),
                self._usage(project_id="/repo/a", session_id="s2", tokens=20, cost=0.2),
                self._usage(project_id="/repo/b", session_id="s3", tokens=30, cost=0.3),
            ],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )
        self.assertEqual(stats.project_breakdown, {
            "/repo/a": self._bucket((10, 0.1), (20, 0.2)),
            "/repo/b": self._bucket((30, 0.3)),
        })
        self.assertEqual(stats.session_breakdown, {
            "s1": self._bucket((10, 0.1)),
            "s2": self._bucket((20, 0.2)),
            "s3": self._bucket((30, 0.3)),
        })

    def test_unattributed_rows_fall_into_unknown_bucket(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [self._usage(), self._usage()],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )
        self.assertEqual(list(stats.project_breakdown), ["unknown"])
        self.assertEqual(list(stats.session_breakdown), ["unknown"])
        self.assertEqual(stats.project_breakdown["unknown"]["input"], 200)

    def test_terminal_shows_grouping_sections_and_unattributed_note(self):
        attributed = app.UsageAnalyzer.analyze_agent(
            "pi", [self._usage(project_id="/repo/a", session_id="s1")],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            app.print_single_agent_report(self._report(attributed), "pi")
        terminal = output.getvalue()
        self.assertIn("BREAKDOWN BY SESSION", terminal)
        self.assertIn("s1       100", terminal)
        self.assertIn("BREAKDOWN BY PROJECT", terminal)
        self.assertIn("/repo/a  100", terminal)

        unattributed = app.UsageAnalyzer.analyze_agent(
            "pi", [self._usage()],
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            app.print_single_agent_report(self._report(unattributed), "pi")
        terminal = output.getvalue()
        self.assertIn("No session attribution available.", terminal)
        self.assertIn("No project attribution available.", terminal)


class DisplayPeriodTests(unittest.TestCase):
    """Direct coverage for UsageAnalyzer.display_period (all-time branches)."""

    @staticmethod
    def _usage(timestamp):
        return app.UsageEntry(
            agent="pi", model_id="model", timestamp=timestamp,
            input_tokens=1, output_tokens=0, cache_read_tokens=0,
            cache_write_tokens=0, total_tokens=1, cost=0.0,
            cost_breakdown={}, provider="openai", cost_status="unknown",
        )

    def test_ranged_mode_returns_the_requested_period(self):
        display = app.UsageAnalyzer.display_period(
            [self._usage("2026-08-03T00:00:00Z")],
            date(2026, 8, 1), date(2026, 8, 5), "5d", is_all_time=False,
        )
        self.assertEqual(
            display, models.AgentDisplay(date(2026, 8, 1), date(2026, 8, 5), "5d")
        )

    def test_all_time_pins_to_first_usage_date(self):
        display = app.UsageAnalyzer.display_period(
            [self._usage("2026-08-03T00:00:00Z"), self._usage("2026-08-01T00:00:00Z")],
            None, date(2026, 8, 31), "ALL TIME", is_all_time=True,
        )
        self.assertEqual(
            display,
            models.AgentDisplay(date(2026, 8, 1), date(2026, 8, 31),
                                "ALL TIME (data from 2026-08-01)"),
        )

    def test_all_time_unparseable_timestamps_fall_back_to_end_date(self):
        display = app.UsageAnalyzer.display_period(
            [self._usage("not-a-date")],
            None, date(2026, 8, 31), "ALL TIME", is_all_time=True,
        )
        self.assertEqual(
            display, models.AgentDisplay(date(2026, 8, 31), date(2026, 8, 31), "ALL TIME")
        )


class AggregateTimestampTests(unittest.TestCase):
    """Date-only stats-cache stamps still drive rates and period pinning."""

    @staticmethod
    def _aggregate(timestamp="2026-08-01"):
        return app.UsageEntry(
            agent="claude-code", model_id="model", timestamp=timestamp,
            input_tokens=10, output_tokens=20, cache_read_tokens=0,
            cache_write_tokens=0, total_tokens=30, cost=1.5,
            cost_breakdown={"total": 1.5}, provider="anthropic",
            cost_status="recorded", is_aggregated=True, model_requests=1,
        )

    def test_date_only_timestamp_parses(self):
        self.assertEqual(
            app.UsageAnalyzer.extract_date_from_timestamp("2026-08-01"),
            date(2026, 8, 1),
        )

    def test_all_time_aggregate_gets_rates_without_daily_rows(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "claude-code", [self._aggregate()],
            None, date(2026, 8, 31), "ALL TIME",
        )
        self.assertEqual(stats.daily_activity, {})
        self.assertEqual(stats.daily_cost, 1.5 / 31)
        self.assertEqual(stats.yearly_cost, 1.5 / 31 * 365)

    def test_all_time_period_pins_to_date_only_timestamp(self):
        display = app.UsageAnalyzer.display_period(
            [self._aggregate()], None, date(2026, 8, 31), "ALL TIME", True,
        )
        self.assertEqual(display.start_date, date(2026, 8, 1))

    def test_terminal_marks_active_day_rates_as_not_applicable_without_daily_rows(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "claude-code", [self._aggregate()],
            None, date(2026, 8, 31), "ALL TIME",
        )
        report = app.AnalysisReport(
            start_date=None, end_date=date(2026, 8, 31), period_label="ALL TIME",
            agent_stats={"claude-code": stats},
            agent_displays={"claude-code": models.AgentDisplay(
                date(2026, 8, 1), date(2026, 8, 31), "ALL TIME (data from 2026-08-01)")},
        )
        output = io.StringIO()
        with redirect_stdout(output):
            app.print_single_agent_report(report, "claude-code")
        terminal = output.getvalue()
        self.assertIn("Daily (active days only, 0 days):", terminal)
        self.assertIn("n/a", terminal)
        self.assertIn("No daily activity data available.", terminal)


class PacingTests(unittest.TestCase):
    def test_monthly_pacing_uses_calendar_days_and_known_cost(self):
        usage = app.UsageEntry(
            agent="codex", model_id="model", timestamp="2026-09-10T00:00:00Z",
            input_tokens=1, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
            total_tokens=1, cost=50.0, cost_breakdown={"total": 50.0},
            provider="openai", billing_mode="metered", cost_status="recorded",
        )
        stats = app.UsageAnalyzer.analyze_agent(
            "codex", [usage], date(2026, 9, 1), date(2026, 9, 10), "10d"
        )
        result = app.UsageAnalyzer.pacing(stats, [usage], {"usd": 100, "period": "month"}, date(2026, 9, 10))
        self.assertEqual(result["period_start"], "2026-09-01")
        self.assertEqual(result["period_end"], "2026-09-30")
        self.assertEqual(result["remaining_usd"], 50.0)
        self.assertEqual(result["status"], "ahead")

    def test_pacing_is_unavailable_for_unknown_metered_cost(self):
        usage = app.UsageEntry(
            agent="codex", model_id="model", timestamp="2026-09-10T00:00:00Z",
            input_tokens=1, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
            total_tokens=1, cost=0.0, cost_breakdown={}, billing_mode="metered",
            cost_status="unknown",
        )
        stats = app.UsageAnalyzer.analyze_agent(
            "codex", [usage], date(2026, 9, 1), date(2026, 9, 10), "10d"
        )
        self.assertEqual(
            app.UsageAnalyzer.pacing(stats, [usage], {"usd": 100, "period": "month"}, date(2026, 9, 10)),
            {"status": "unavailable", "reason": "unknown metered cost"},
        )


class ActivityRatioTests(unittest.TestCase):
    """Phase 4.1: request/turn/tool ratios, including the metric-row split."""

    @staticmethod
    def _usage(requests=0, turns=0, tool_calls=0, metric_only=False):
        return app.UsageEntry(
            agent="pi", model_id="model", timestamp="2026-08-01T00:00:00Z",
            input_tokens=10, output_tokens=0, cache_read_tokens=0,
            cache_write_tokens=0, total_tokens=10, cost=0.0,
            cost_breakdown={}, provider="openai", cost_status="not_applicable",
            is_metric_only=metric_only, model_requests=requests,
            model_turns=turns, model_tool_calls=tool_calls,
        )

    @staticmethod
    def _analyze(entries):
        return app.UsageAnalyzer.analyze_agent(
            "pi", entries, date(2026, 8, 1), date(2026, 8, 1), "1d",
        )

    def test_ratios_divide_across_usage_and_metric_rows(self):
        stats = self._analyze([
            self._usage(requests=2), self._usage(turns=1, tool_calls=6, metric_only=True),
        ])
        self.assertEqual(stats.requests_per_turn, 2.0)
        self.assertEqual(stats.tool_calls_per_request, 3.0)
        self.assertEqual(stats.tool_calls_per_turn, 6.0)

    def test_ratios_stay_none_without_activity_rows(self):
        stats = self._analyze([self._usage()])
        self.assertEqual(
            (stats.requests_per_turn, stats.tool_calls_per_request,
             stats.tool_calls_per_turn), (None, None, None),
        )
        report = app.AnalysisReport(
            start_date=date(2026, 8, 1), end_date=date(2026, 8, 1), period_label="1d",
            agent_stats={"pi": stats},
            agent_displays={"pi": models.AgentDisplay(
                date(2026, 8, 1), date(2026, 8, 1), "1d")},
        )
        output = io.StringIO()
        with redirect_stdout(output):
            app.print_single_agent_report(report, "pi")
        self.assertIn("Ratios: N/A (no request, turn, or tool rows in scope)",
                      output.getvalue())


class PreferencesTests(unittest.TestCase):
    @staticmethod
    def _usage(provider="openai", model="gpt-5.6"):
        return app.UsageEntry(
            agent="codex", model_id=model, timestamp="2026-08-01T12:00:00Z",
            input_tokens=10, output_tokens=0, cache_read_tokens=0,
            cache_write_tokens=0, total_tokens=10, cost=0.0,
            cost_breakdown={}, provider=provider,
        )

    def test_provider_preferences_override_agent_default(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "preferences.jsonc"
            path.write_text(json.dumps({"schemaVersion": 2, "agents": {
                "claude-code": {"billingMode": "unknown"},
                "codex": {
                    "billingMode": "unknown",
                    "providers": {
                        "openai": {"billingMode": "subscription"},
                        "amazon-bedrock": {"billingMode": "metered", "pricing": {
                            "source": "amazon-bedrock", "otherSources": ["models-dev"],
                        }},
                    },
                },
                "opencode": {"billingMode": "unknown"},
                "pi": {"billingMode": "unknown"},
            }}))
            preferences = app.PreferencesResolver(path)
            usage = self._usage(provider="amazon-bedrock")
            preferences.apply(usage)

        self.assertEqual(usage.observed_provider, "amazon-bedrock")
        self.assertEqual(usage.provider, "amazon-bedrock")
        self.assertEqual(usage.billing_mode, "metered")
        self.assertEqual(usage.pricing_provider, "amazon-bedrock")
        self.assertEqual(usage.pricing_model, "gpt-5.6")
        self.assertEqual(usage.pricing_sources, ["amazon-bedrock", "models-dev"])

    def test_agent_default_applies_to_all_claude_models_without_model_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "preferences.jsonc"
            path.write_text(json.dumps({"schemaVersion": 2, "agents": {
                "claude-code": {
                    "provider": "amazon-bedrock",
                    "billingMode": "metered",
                    "pricing": {
                        "source": "amazon-bedrock",
                        "otherSources": ["models-dev", "pi-models-store"],
                    },
                },
                "codex": {"billingMode": "unknown"},
                "opencode": {"billingMode": "unknown"},
                "pi": {"billingMode": "unknown"},
            }}))
            preferences = app.PreferencesResolver(path)
            usage = self._usage(provider=None, model="claude-sonnet-4-6")
            usage.agent = "claude-code"
            preferences.apply(usage)

        self.assertEqual(usage.provider, "amazon-bedrock")
        self.assertEqual(usage.billing_mode, "metered")
        self.assertEqual(usage.pricing_provider, "amazon-bedrock")
        self.assertEqual(usage.pricing_sources,
                         ["amazon-bedrock", "models-dev", "pi-models-store"])

    def test_model_id_rules_override_only_matching_litellm_models(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "preferences.jsonc"
            path.write_text(json.dumps({"agents": {"codex": {"providers": {
                "litellm": {"billingMode": "unknown", "modelIdRules": [
                    {"prefix": "local.", "billingMode": "local"},
                    {"exact": "local.cloud", "billingMode": "subscription"},
                    {"prefix": "bedrock.", "provider": "amazon-bedrock", "billingMode": "metered", "pricing": {"source": "amazon-bedrock"}},
                ]},
            }}}}))
            preferences = app.PreferencesResolver(path)
            local = self._usage("litellm", "local.qwen")
            exact = self._usage("litellm", "local.cloud")
            cloud = self._usage("litellm", "bedrock.claude")
            unknown = self._usage("litellm", "gpt")
            for usage in (local, exact, cloud, unknown):
                preferences.apply(usage)

        self.assertEqual((local.billing_mode, exact.billing_mode, unknown.billing_mode), ("local", "subscription", "unknown"))
        self.assertEqual((cloud.provider, cloud.billing_mode, cloud.pricing_sources), ("amazon-bedrock", "metered", ["amazon-bedrock"]))

    def test_subscription_usage_is_not_priced_even_when_price_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            preferences_path = root / "preferences.jsonc"
            preferences_path.write_text(json.dumps({"schemaVersion": 2, "agents": {
                "claude-code": {"billingMode": "unknown"},
                "codex": {"billingMode": "subscription"},
                "opencode": {"billingMode": "unknown"},
                "pi": {"billingMode": "unknown"},
            }}))
            pricing_path = root / "pricing.jsonc"
            pricing_path.write_text(json.dumps({"overrides": {
                "openai/gpt-5.6": {"input": 1, "output": 1},
            }}))
            usage = self._usage()
            app.apply_pricing(
                [usage], app.PricingResolver(pricing_path, root / "cache"),
                app.PreferencesResolver(preferences_path),
            )

        self.assertEqual(usage.billing_mode, "subscription")
        self.assertEqual(usage.cost_status, "not_applicable")
        self.assertEqual(usage.cost, 0.0)

    def test_budget_provider_override_and_invalid_budget_diagnostic(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "preferences.jsonc"
            path.write_text(json.dumps({"agents": {
                "codex": {"budget": {"usd": 100, "period": "month"},
                          "providers": {"openai": {"budget": {"usd": 25, "period": "week"}},
                                         "bad": {"budget": {"usd": -1, "period": "day"}}}},
            }}))
            preferences = app.PreferencesResolver(path)
            self.assertEqual(preferences.budget_for("codex", "openai"),
                             {"usd": 25.0, "period": "week"})
            self.assertIsNone(preferences.budget_for("codex", "bad"))
            self.assertTrue(preferences.warnings)

    def test_budget_groups_replace_agent_budget_for_overridden_provider(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "preferences.jsonc"
            path.write_text(json.dumps({"agents": {"codex": {
                "budget": {"usd": 100, "period": "month"},
                "providers": {"openai": {"budget": {"usd": 25, "period": "week"}}},
            }}}))
            groups = app.PreferencesResolver(path).budget_groups("codex", {"openai", "bedrock"})
        self.assertEqual(groups, {"openai": {"usd": 25.0, "period": "week"},
                                  None: {"usd": 100.0, "period": "month"}})

    def test_invalid_model_id_rule_warns_and_uses_provider_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "preferences.jsonc"
            path.write_text(json.dumps({"agents": {"codex": {"providers": {
                "litellm": {"billingMode": "unknown", "modelIdRules": [
                    {"exact": "local.qwen", "prefix": "local.", "billingMode": "local"},
                ]},
            }}}}))
            preferences = app.PreferencesResolver(path)
            usage = self._usage("litellm", "local.qwen")
            preferences.apply(usage)

        self.assertEqual(usage.billing_mode, "unknown")
        self.assertTrue(preferences.warnings)

    def test_duplicate_exact_model_id_rules_warn_and_use_provider_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "preferences.jsonc"
            path.write_text(json.dumps({"agents": {"codex": {"providers": {
                "litellm": {"billingMode": "unknown", "modelIdRules": [
                    {"exact": "local.qwen", "billingMode": "local"},
                    {"exact": "local.qwen", "billingMode": "subscription"},
                ]},
            }}}}))
            preferences = app.PreferencesResolver(path)
            usage = self._usage("litellm", "local.qwen")
            preferences.apply(usage)

        self.assertEqual(usage.billing_mode, "unknown")
        self.assertTrue(preferences.warnings)

    def test_model_rule_budget_is_separate_from_provider_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "preferences.jsonc"
            path.write_text(json.dumps({"agents": {"codex": {"providers": {
                "litellm": {"budget": {"usd": 100, "period": "month"}, "modelIdRules": [
                    {"prefix": "local.", "billingMode": "local", "budget": {"usd": 10, "period": "week"}},
                ]},
            }}}}))
            preferences = app.PreferencesResolver(path)
            proxy = self._usage("litellm", "cloud.model")
            local = self._usage("litellm", "local.qwen")
            preferences.apply(proxy)
            preferences.apply(local)

        self.assertEqual(preferences.budget_groups_for_usages([proxy, local]), {
            "litellm": {"usd": 100.0, "period": "month"},
            "litellm/prefix:local.": {"usd": 10.0, "period": "week"},
        })

    def test_invalid_provider_policy_emits_a_diagnostic(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "preferences.jsonc"
            path.write_text(json.dumps({"schemaVersion": 2, "agents": {
                "claude-code": {"billingMode": "unknown"},
                "codex": {"billingMode": "unknown", "providers": {"openai": "subscription"}},
                "opencode": {"billingMode": "unknown"},
                "pi": {"billingMode": "unknown"},
            }}))
            preferences = app.PreferencesResolver(path)
            usage = self._usage()
            preferences.apply(usage)

        self.assertEqual(usage.billing_mode, "unknown")
        self.assertTrue(preferences.warnings)


class DateRangeTests(unittest.TestCase):
    def test_command_after_agent_is_not_consumed_as_an_agent(self):
        args = app.parse_args(["--agent", "codex", "report"])

        self.assertEqual(args.command, "report")
        self.assertEqual(args.agent, ["codex"])

    @staticmethod
    def _parse(*arguments):
        with patch("sys.argv", ["eurysx", *arguments]):
            return app.parse_args()

    def test_rolling_days_include_today(self):
        start, end, label = app.get_date_range(
            self._parse("--days", "3"), today=date(2026, 8, 3)
        )

        self.assertEqual((start, end, label), (date(2026, 8, 1), date(2026, 8, 3), "3d"))

    def test_calendar_selectors_use_inclusive_boundaries(self):
        cases = [
            (("--from", "2026-08-02", "--to", "2026-08-04"), date(2026, 8, 2), date(2026, 8, 4), "2026-08-02 to 2026-08-04"),
            (("--month", "2026-02"), date(2026, 2, 1), date(2026, 2, 28), "2026-02"),
            (("--quarter", "2026-Q2"), date(2026, 4, 1), date(2026, 6, 30), "2026-Q2"),
            (("--year", "2024"), date(2024, 1, 1), date(2024, 12, 31), "2024"),
            (("--ytd",), date(2026, 1, 1), date(2026, 8, 3), "YTD"),
        ]
        for arguments, expected_start, expected_end, expected_label in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(
                    app.get_date_range(self._parse(*arguments), today=date(2026, 8, 3)),
                    (expected_start, expected_end, expected_label),
                )

    def test_period_selectors_reject_conflicting_or_invalid_input(self):
        for arguments in (
            ("--days", "0"),
            ("--days", "1", "--weeks", "1"),
            ("--to", "2026-08-03"),
            ("--from", "2026-08-04", "--to", "2026-08-03"),
            ("--quarter", "2026-Q5"),
        ):
            with self.subTest(arguments=arguments), patch("sys.argv", ["eurysx", *arguments]):
                with self.assertRaises(SystemExit):
                    app.parse_args()

    def test_simulated_cli_periods_work_for_every_supported_agent_selector(self):
        agents = ("claude-code", "opencode", "pi", "codex")
        periods = (
            (), ("--days", "1"), ("--weeks", "1"),
            ("--from", "2026-08-01", "--to", "2026-08-03"),
            ("--month", "2026-08"), ("--quarter", "2026-Q3"),
            ("--year", "2026"), ("--ytd",),
        )

        def entries(agent):
            return [app.UsageEntry(
                agent=agent, model_id="model", timestamp="2026-08-01T12:00:00Z",
                input_tokens=1, output_tokens=0, cache_read_tokens=0,
                cache_write_tokens=0, total_tokens=1, cost=1.0,
                cost_breakdown={"total": 1.0}, cost_status="recorded",
            )]

        def fake_sources(agent, home=None):
            return [Source(f"{agent}:fake", "fingerprint-1", "1", lambda: entries(agent))]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            resolver = app.PricingResolver(root / "missing.jsonc", root / "cache")
            patches = (
                patch.object(app, "detect_agents", return_value=list(agents)),
                patch.object(app, "collect_sources", side_effect=fake_sources),
                patch.object(app, "PricingResolver", return_value=resolver),
                patch.object(app, "get_eurysx_data_dir", return_value=root / "data"),
            )
            with patches[0], patches[1], patches[2], patches[3]:
                for selected_agent in ("all", *agents):
                    for index, period in enumerate(periods):
                        output_path = root / f"{selected_agent}-{index}.json"
                        with patch("sys.argv", [
                            "eurysx", "--agent", selected_agent, *period,
                            "--output", str(output_path),
                        ]), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                            app.main()
                        report = json.loads(output_path.read_text())
                        expected = list(agents) if selected_agent == "all" else [selected_agent]
                        self.assertEqual(report["agents_analyzed"], expected)

    def test_simulated_cli_selectors_filter_report(self):
        def entries():
            return [
                app.UsageEntry(
                    agent="pi", model_id="model-a", timestamp="2026-08-01T12:00:00Z",
                    input_tokens=1, output_tokens=0, cache_read_tokens=0,
                    cache_write_tokens=0, total_tokens=10, cost=1.0,
                    cost_breakdown={"total": 1.0}, cost_status="recorded",
                    provider="openai",
                ),
                app.UsageEntry(
                    agent="pi", model_id="model-b", timestamp="2026-08-02T12:00:00Z",
                    input_tokens=1, output_tokens=0, cache_read_tokens=0,
                    cache_write_tokens=0, total_tokens=20, cost=2.0,
                    cost_breakdown={"total": 2.0}, cost_status="recorded",
                    provider="anthropic",
                ),
            ]

        def fake_sources(agent, home=None):
            return [Source(f"{agent}:fake", "fingerprint-1", "1", entries)]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            resolver = app.PricingResolver(root / "missing.jsonc", root / "cache")
            patches = (
                patch.object(app, "detect_agents", return_value=["pi"]),
                patch.object(app, "collect_sources", side_effect=fake_sources),
                patch.object(app, "PricingResolver", return_value=resolver),
                patch.object(app, "get_eurysx_data_dir", return_value=root / "data"),
            )
            with patches[0], patches[1], patches[2], patches[3]:
                cases = (
                    (("--model", "model-a"), 10),
                    (("--provider", "anthropic"), 20),
                    (("--model", "model-a", "--provider", "openai"), 10),
                    (("--model", "model-b", "--provider", "openai"), 0),
                    # Recorded cost flips policy billing to metered, so the
                    # post-pricing hook excludes these rows for 'unknown'.
                    (("--billing-mode", "metered"), 30),
                    (("--billing-mode", "unknown"), 0),
                )
                for index, (selector, expected_total) in enumerate(cases):
                    output_path = root / f"{index}.json"
                    with patch("sys.argv", [
                        "eurysx", "--agent", "pi", *selector,
                        "--output", str(output_path),
                    ]), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                        app.main()
                    report = json.loads(output_path.read_text())
                    self.assertEqual(
                        report["agent_stats"]["pi"]["total_tokens"], expected_total
                    )

    def test_previous_window_same_length_before_current(self):
        cases = [
            (date(2026, 8, 1), date(2026, 8, 3), (date(2026, 7, 29), date(2026, 7, 31))),
            (None, date(2026, 8, 3), (None, None)),
            (date(2026, 2, 1), date(2026, 2, 28), (date(2026, 1, 4), date(2026, 1, 31))),
            (date(2026, 8, 3), date(2026, 8, 3), (date(2026, 8, 2), date(2026, 8, 2))),
        ]
        for start, end, expected in cases:
            with self.subTest(start=start):
                self.assertEqual(app._previous_window(start, end), expected)

    def test_simulated_cli_period_comparison_uses_previous_window(self):
        def entries():
            return [
                app.UsageEntry(
                    agent="pi", model_id="model", timestamp="2026-07-31T12:00:00Z",
                    input_tokens=1, output_tokens=0, cache_read_tokens=0,
                    cache_write_tokens=0, total_tokens=40, cost=4.0,
                    cost_breakdown={"total": 4.0}, cost_status="recorded",
                    provider="openai",
                ),
                app.UsageEntry(
                    agent="pi", model_id="model", timestamp="2026-08-01T12:00:00Z",
                    input_tokens=1, output_tokens=0, cache_read_tokens=0,
                    cache_write_tokens=0, total_tokens=100, cost=1.0,
                    cost_breakdown={"total": 1.0}, cost_status="recorded",
                    provider="openai",
                ),
            ]

        def fake_sources(agent, home=None):
            return [Source(f"{agent}:fake", "fingerprint-1", "1", entries)]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            resolver = app.PricingResolver(root / "missing.jsonc", root / "cache")
            patches = (
                patch.object(app, "detect_agents", return_value=["pi"]),
                patch.object(app, "collect_sources", side_effect=fake_sources),
                patch.object(app, "PricingResolver", return_value=resolver),
                patch.object(app, "get_eurysx_data_dir", return_value=root / "data"),
            )
            output_path = root / "report.json"
            terminal = io.StringIO()
            with patches[0], patches[1], patches[2], patches[3], \
                    patch("sys.argv", [
                        "eurysx", "--agent", "pi",
                        "--from", "2026-08-01", "--to", "2026-08-01",
                        "--output", str(output_path),
                    ]), redirect_stdout(terminal):
                app.main()
            report = json.loads(output_path.read_text())
            comparison = report["period_comparison"]["pi"]
            terminal_text = terminal.getvalue()

        self.assertEqual(comparison["current"]["total_tokens"], 100)
        self.assertEqual(comparison["previous"]["total_tokens"], 40)
        self.assertEqual(comparison["current"]["known_cost"], 1.0)
        self.assertEqual(comparison["previous"]["known_cost"], 4.0)
        self.assertNotIn("cost_status_counts", comparison["current"])
        self.assertNotIn("cost_status_counts", comparison["previous"])
        self.assertEqual(comparison["previous_period"], "2026-07-31 to 2026-07-31")
        self.assertIn("PERIOD COMPARISON", terminal_text)
        self.assertIn("+150%", terminal_text)  # (100 - 40) / 40

    def test_selected_ranges_exclude_claude_aggregate_and_warn(self):
        aggregate = app.UsageEntry(
            agent="claude-code", model_id="claude-sonnet-4", timestamp="2026-08-01",
            input_tokens=10, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
            total_tokens=10, cost=1.0, cost_breakdown={"total": 1.0},
            cost_status="recorded", is_aggregated=True,
        )
        stats = app.UsageAnalyzer.analyze_agent(
            "claude-code", [aggregate], date(2026, 8, 1), date(2026, 8, 3), "3d",
            include_aggregated=False, aggregates_present=True,
        )

        self.assertEqual(stats.usage_entries, 0)
        self.assertTrue(stats.scope_warnings)

    def test_aggregate_warning_absent_without_aggregates_present(self):
        usage = app.UsageEntry(
            agent="pi", model_id="model", timestamp="2026-08-01T12:00:00Z",
            input_tokens=1, output_tokens=0, cache_read_tokens=0,
            cache_write_tokens=0, total_tokens=1, cost=0.0, cost_breakdown={},
        )
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [usage], date(2026, 8, 1), date(2026, 8, 1), "1d",
            include_aggregated=False, aggregates_present=False,
        )

        self.assertEqual(stats.scope_warnings, [])

    def test_json_report_excludes_claude_aggregate_for_selected_range(self):
        aggregate = app.UsageEntry(
            agent="claude-code", model_id="claude-sonnet-4", timestamp="2026-08-01",
            input_tokens=10, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
            total_tokens=10, cost=1.0, cost_breakdown={"total": 1.0},
            cost_status="recorded", is_aggregated=True,
        )
        with tempfile.TemporaryDirectory() as temp:
            output_path = Path(temp) / "report.json"
            terminal = io.StringIO()
            with patch("sys.argv", [
                "eurysx", "--agent", "claude-code", "--days", "3", "--output", str(output_path),
            ]), patch.object(app, "collect_sources", side_effect=lambda agent, home=None: [
                    Source("claude-code:fake", "fingerprint-1", "1", lambda: [aggregate])]), \
                    patch.object(app, "get_eurysx_data_dir", return_value=Path(temp) / "data"), \
                    redirect_stdout(terminal):
                app.main()
            report = json.loads(output_path.read_text())

        stats = report["agent_stats"]["claude-code"]
        self.assertEqual(stats["usage_entries"], 0)
        self.assertIsNone(stats["priced_token_coverage"])
        self.assertIn("metered_tokens", stats)
        self.assertIn("non_metered_tokens", stats)
        self.assertIn("route_breakdown", stats)
        self.assertTrue(stats["scope_warnings"])
        self.assertIn("preferences", report)
        self.assertIn("Excluded aggregate usage", terminal.getvalue())


class Act3Phase1BaselineTests(unittest.TestCase):
    """Pre-refactor baseline: locks the report shape Act III Phase 2 and 6 diff against."""

    TOP_LEVEL_KEYS = [
        "agent_stats", "agents_analyzed", "analysis_period", "comparison_summary", "period_comparison",
        "preferences", "pricing", "schema_version",
    ]
    AGENT_STATS_KEYS = sorted([
        "billing_mode_tokens", "cache_efficiency_ratio", "cache_read_ratio",
        "cost_status_counts", "daily_activity", "daily_cost", "known_cost",
        "metered_tokens", "model_breakdown", "model_requests", "model_tool_calls",
        "model_turns", "monthly_cost", "non_metered_tokens", "priced_token_coverage",
        "pricing_fetched_at", "pricing_source_kinds", "pricing_sources", "project_breakdown",
        "pacing", "quarterly_cost", "requests_per_turn", "route_breakdown", "scope_warnings",
        "session_breakdown", "sessions_count", "tool_calls_per_request",
        "tool_calls_per_turn", "total_cache_read_tokens",
        "total_cache_write_tokens", "total_cost", "total_input_tokens",
        "total_output_tokens", "total_tokens", "unique_models",
        "unknown_cost_count",
        "unknown_cost_tokens", "unresolved_routes", "usage_entries", "weekly_cost", "yearly_cost",
    ])
    TERMINAL_SECTIONS = [
        "TOTAL USAGE (ALL MODELS)", "BREAKDOWN BY MODEL",
        "BREAKDOWN BY SESSION", "BREAKDOWN BY PROJECT",
        "COST PROJECTIONS PER TIME PERIOD", "TOKEN VOLUME PER TIME PERIOD",
        "MODEL ACTIVITY VOLUME PER TIME PERIOD", "DAILY ACTIVITY",
        "SUMMARY STATISTICS", "CACHE EFFECTIVENESS", "COST ANALYSIS",
        "PERIOD COMPARISON",
    ]

    EXPECTED_PI_STATS = {
        "billing_mode_tokens": {"metered": 100},
        "cache_efficiency_ratio": 0.75,
        "cache_read_ratio": 30 / 70,
        "cost_status_counts": {"recorded": 1},
        "daily_activity": {"2026-08-01": {"cost": 1.25, "cost_status_counts": {"recorded": 1}, "tokens": 100}},
        "daily_cost": 1.25,
        "known_cost": 1.25,
        "metered_tokens": 100,
        "model_breakdown": {"model": {
            "cache_read": 30, "cache_write": 40, "cost": 1.25, "cost_status_counts": {"recorded": 1}, "input": 10,
            "model_requests": 1, "model_tool_calls": 0, "model_turns": 1, "output": 20,
        }},
        "model_requests": 1,
        "model_tool_calls": 0,
        "model_turns": 1,
        "monthly_cost": 37.5,
        "non_metered_tokens": {},
        "priced_token_coverage": 1.0,
        "pricing_fetched_at": {},
        "pricing_source_kinds": {"recorded": "recorded"},
        "pricing_sources": ["recorded"],
        "quarterly_cost": 112.5,
        "requests_per_turn": 1.0,
        "route_breakdown": {"openai/model [metered]": {
            "cost": 1.25, "cost_status_counts": {"recorded": 1}, "entries": 1, "observed_providers": ["openai"], "model_requests": 1,
            "model_tool_calls": 0, "model_turns": 1, "tokens": 100,
        }},
        "scope_warnings": [],
        "unresolved_routes": [],
        "pacing": {},
        "sessions_count": 1,
        "total_cache_read_tokens": 30,
        "total_cache_write_tokens": 40,
        "total_cost": 1.25,
        "total_input_tokens": 10,
        "total_output_tokens": 20,
        "tool_calls_per_request": 0.0,
        "tool_calls_per_turn": 0.0,
        "total_tokens": 100,
        "unique_models": ["model"],
        "unknown_cost_count": 0,
        "unknown_cost_tokens": 0,
        "usage_entries": 1,
        "weekly_cost": 8.75,
        "yearly_cost": 456.25,
        "project_breakdown": {"/repo/a": {
            "cache_read": 30, "cache_write": 40, "cost": 1.25, "cost_status_counts": {"recorded": 1}, "input": 10,
            "model_requests": 1, "model_tool_calls": 0, "model_turns": 1, "output": 20,
        }},
        "session_breakdown": {"s1": {
            "cache_read": 30, "cache_write": 40, "cost": 1.25, "cost_status_counts": {"recorded": 1}, "input": 10,
            "model_requests": 1, "model_tool_calls": 0, "model_turns": 1, "output": 20,
        }},
    }

    def _run(self, output_format="json", recorded=True, default_html=False):
        cost = 1.25 if recorded else 0.0
        usage = app.UsageEntry(
            agent="pi", model_id="model", timestamp="2026-08-01T12:00:00Z",
            input_tokens=10, output_tokens=20, cache_read_tokens=30,
            cache_write_tokens=40, total_tokens=100, cost=cost,
            cost_breakdown={"total": cost} if recorded else {}, provider="openai",
            observed_provider="openai", cost_status="recorded" if recorded else "unknown", session_id="s1",
            project_id="/repo/a", model_requests=1, model_turns=1,
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output_path = root / f"report.{output_format}"
            terminal = io.StringIO()
            # Hermetic pricing/preferences: no repo config leaks into the baseline.
            pricing = app.PricingResolver(root / "missing.jsonc", root / "cache")
            prefs = app.PreferencesResolver(root / "missing-prefs.jsonc")
            argv = [
                "eurysx", "--agent", "pi",
                "--from", "2026-08-01", "--to", "2026-08-01",
                "--format", output_format,
            ]
            if not default_html:
                argv += ["--output", str(output_path)]
            sources = lambda agent, home=None: [
                Source("pi:fake", "fingerprint-1", "1", lambda: [usage])
            ]
            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                with (
                    patch("sys.argv", argv),
                    patch.object(app, "collect_sources", side_effect=sources),
                    patch.object(app, "get_eurysx_data_dir", return_value=root / "data"),
                    patch.object(app, "PricingResolver", return_value=pricing),
                    patch.object(app, "PreferencesResolver", return_value=prefs),
                    redirect_stdout(terminal),
                    redirect_stderr(io.StringIO()),
                ):
                    app.main()
            finally:
                os.chdir(previous_cwd)
            if default_html:
                output_path = next(root.glob("usage-analysis-report-*"))
            if output_format == "html":
                content = "\n".join(path.read_text() for path in sorted(output_path.glob("*.html")))
            else:
                content = output_path.read_text()
            return (json.loads(content) if output_format == "json" else content), terminal.getvalue()

    def test_html_output_is_written_via_cli(self):
        html, _ = self._run("html", recorded=False)

        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("PRICING PROVENANCE", html)
        self.assertIn("N/A", html)
        comparison = html.split("<summary>PERIOD COMPARISON</summary>", 1)[1].split("</details>", 1)[0]
        self.assertIn("N/A", comparison)
        self.assertNotIn("$0.000000", comparison)

    def test_html_output_defaults_to_a_timestamped_directory(self):
        html, _ = self._run("html", default_html=True)

        self.assertIn("Usage overview", html)
        self.assertIn("PI CODING AGENT", html)

    def test_json_output_shape_is_the_locked_baseline(self):
        report, _ = self._run()
        self.assertEqual(sorted(report), self.TOP_LEVEL_KEYS)
        stats = report["agent_stats"]["pi"]
        self.assertEqual(sorted(stats), self.AGENT_STATS_KEYS)

    def test_json_values_are_the_locked_baseline(self):
        report, _ = self._run()
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["agents_analyzed"], ["pi"])
        self.assertEqual(report["analysis_period"], {
            "start": "2026-08-01", "end": "2026-08-01",
            "label": "2026-08-01 to 2026-08-01",
        })
        self.assertEqual(report["agent_stats"]["pi"], self.EXPECTED_PI_STATS)

    def test_cache_ratios_are_present_in_json_and_match_the_terminal(self):
        report, terminal = self._run()
        stats = report["agent_stats"]["pi"]
        self.assertAlmostEqual(stats["cache_read_ratio"], 30 / 70)
        self.assertEqual(stats["cache_efficiency_ratio"], 0.75)
        self.assertIn("Cache read ratio:", terminal)
        self.assertIn("42.9% (30 / 70)", terminal)
        self.assertIn("Cache efficiency ratio:", terminal)
        self.assertIn("0.8:1", terminal)

    def test_terminal_report_key_sections_present(self):
        _, terminal = self._run()
        for section in self.TERMINAL_SECTIONS:
            self.assertIn(section, terminal)


class PricingPathTests(unittest.TestCase):
    def test_defaults_use_the_current_eurysx_working_directory(self):
        root = Path("/workspace/eurysx")

        with patch.object(paths_module.Path, "cwd", return_value=root):
            self.assertEqual(
                paths_module.get_eurysx_dirs(environ={}),
                (root / "config", root / "cache"),
            )

    def test_environment_overrides_win_without_creating_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_dir = root / "config-override"
            cache_dir = root / "cache-override"
            config, cache = paths_module.get_eurysx_dirs(
                environ={
                    "EURYSX_CONFIG_DIR": str(config_dir),
                    "EURYSX_CACHE_DIR": str(cache_dir),
                },
                root=root,
            )

            self.assertEqual((config, cache), (config_dir, cache_dir))
            self.assertFalse(config.exists())
            self.assertFalse(cache.exists())

    def test_resolver_uses_default_user_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_dir, cache_dir = root / "config", root / "cache"
            with patch.object(pricing_module, "get_eurysx_dirs", return_value=(config_dir, cache_dir)):
                resolver = app.PricingResolver()

            self.assertEqual(resolver.config_path, config_dir / "pricing.jsonc")
            self.assertEqual(resolver.cache_dir, cache_dir)
            self.assertFalse(config_dir.exists())
            self.assertTrue(cache_dir.exists())


class SummaryOutputTests(unittest.TestCase):
    def test_comparison_labels_cost_as_known(self):
        stats = models.AgentStats(agent="pi", usage_entries=1, total_tokens=100,
                               total_cost=1.5, known_cost=1.5)
        report = app.AnalysisReport(
            start_date=date(2026, 8, 1), end_date=date(2026, 8, 1), period_label="1d",
            agent_stats={"pi": stats},
        )
        output = io.StringIO()
        with redirect_stdout(output):
            app.print_summary_comparison(report)

        self.assertIn("Known Cost", output.getvalue())


class PresentationBoundaryTests(unittest.TestCase):
    def test_presentation_has_no_pipeline_dependencies(self):
        root = Path(__file__).parent.parent / "src" / "eurysx"

        def imports(path):
            tree = ast.parse(path.read_text())
            return {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }

        pipeline_modules = {"analysis", "pricing", "store", "collectors"}
        self.assertFalse(imports(root / "render.py") & pipeline_modules)
        for path in (root / "analysis.py", root / "pricing.py", root / "store.py", *(root / "collectors").glob("*.py")):
            self.assertNotIn("render", imports(path), path)


class ManualDriftTests(unittest.TestCase):
    def test_manual_contract_terms_exist_in_source(self):
        root = Path(__file__).parent.parent
        cli = (root / "src" / "eurysx" / "cli.py").read_text()
        render = (root / "src" / "eurysx" / "render.py").read_text()
        manual = (root / "docs" / "manual.md").read_text()
        for term in ("--agent", "--format", "--output", "doctor", "stored source(s) no longer exist on disk"):
            self.assertIn(term, cli + manual)
        for key in ("schema_version", "unresolved_routes", "pacing"):
            self.assertIn(key, render)
            self.assertIn(key, manual)


class VersionTests(unittest.TestCase):
    def test_version_flags_print_the_current_version(self):
        for flag in ("--version", "-v"):
            with self.subTest(flag=flag), patch("sys.argv", ["eurysx", flag]):
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(SystemExit) as exit_code:
                    app.parse_args()

            self.assertEqual(exit_code.exception.code, 0)
            self.assertEqual(output.getvalue().strip(), "eurysx 0.1.3")

    def test_cli_version_matches_package_metadata(self):
        with (Path(__file__).parent.parent / "pyproject.toml").open("rb") as metadata:
            project = tomllib.load(metadata)["project"]

        self.assertEqual(app.__version__, project["version"])


if __name__ == "__main__":
    unittest.main()
