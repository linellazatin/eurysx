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


FIXTURES = Path(__file__).parent / "tests" / "fixtures"


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
        self.assertEqual(usage.timestamp, "1754056800000")
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
                    redirect_stdout(io.StringIO()):
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
            store.record_failure("pi:/session-1", "parse failed")

            events = store.events(["pi"])

        self.assertEqual(len(events), 1)


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
                redirect_stdout(io.StringIO()):
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
        self.assertIn("last refresh failed for pi:one", warning)

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

    def test_cache_directory_is_created_without_an_enabled_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache = root / "cache"
            app.PricingResolver(root / "pricing.jsonc", cache)
            self.assertTrue(cache.exists())

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
    def _usage(cost_status, cost, total_tokens):
        return app.UsageEntry(
            agent="pi", model_id="model", timestamp="2026-08-01T12:00:00Z",
            input_tokens=total_tokens, output_tokens=0, cache_read_tokens=0,
            cache_write_tokens=0, total_tokens=total_tokens, cost=cost,
            cost_breakdown={"total": cost} if cost_status != "unknown" else {},
            provider="provider", billing_mode="metered", cost_status=cost_status,
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
                        ]), redirect_stdout(io.StringIO()):
                            app.main()
                        report = json.loads(output_path.read_text())
                        expected = list(agents) if selected_agent == "all" else [selected_agent]
                        self.assertEqual(report["agents_analyzed"], expected)

    def test_selected_ranges_exclude_claude_aggregate_and_warn(self):
        aggregate = app.UsageEntry(
            agent="claude-code", model_id="claude-sonnet-4", timestamp="2026-08-01",
            input_tokens=10, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
            total_tokens=10, cost=1.0, cost_breakdown={"total": 1.0},
            cost_status="recorded", is_aggregated=True,
        )
        stats = app.UsageAnalyzer.analyze_agent(
            "claude-code", [aggregate], date(2026, 8, 1), date(2026, 8, 3), "3d",
            include_aggregated=False,
        )

        self.assertEqual(stats.usage_entries, 0)
        self.assertTrue(stats.scope_warnings)

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
        "agent_stats", "agents_analyzed", "analysis_period", "preferences", "pricing",
    ]
    AGENT_STATS_KEYS = [
        "billing_mode_tokens", "cache_efficiency_ratio", "cache_read_ratio",
        "cost_status_counts", "daily_activity", "daily_cost", "known_cost",
        "metered_tokens", "model_breakdown", "model_requests", "model_tool_calls",
        "model_turns", "monthly_cost", "non_metered_tokens", "priced_token_coverage",
        "pricing_fetched_at", "pricing_sources", "quarterly_cost",
        "route_breakdown", "scope_warnings",
        "sessions_count", "total_cache_read_tokens", "total_cache_write_tokens",
        "total_cost", "total_input_tokens", "total_output_tokens", "total_tokens",
        "unique_models", "unknown_cost_count", "unknown_cost_tokens", "usage_entries",
        "weekly_cost", "yearly_cost",
    ]
    TERMINAL_SECTIONS = [
        "TOTAL USAGE (ALL MODELS)", "BREAKDOWN BY MODEL",
        "COST PROJECTIONS PER TIME PERIOD", "TOKEN VOLUME PER TIME PERIOD",
        "MODEL ACTIVITY VOLUME PER TIME PERIOD", "DAILY ACTIVITY",
        "SUMMARY STATISTICS", "CACHE EFFECTIVENESS", "COST ANALYSIS",
    ]

    EXPECTED_PI_STATS = {
        "billing_mode_tokens": {"metered": 100},
        "cache_efficiency_ratio": 0.75,
        "cache_read_ratio": 30 / 70,
        "cost_status_counts": {"recorded": 1},
        "daily_activity": {"2026-08-01": {"cost": 1.25, "tokens": 100}},
        "daily_cost": 1.25,
        "known_cost": 1.25,
        "metered_tokens": 100,
        "model_breakdown": {"model": {
            "cache_read": 30, "cache_write": 40, "cost": 1.25, "input": 10,
            "model_requests": 1, "model_tool_calls": 0, "model_turns": 1, "output": 20,
        }},
        "model_requests": 1,
        "model_tool_calls": 0,
        "model_turns": 1,
        "monthly_cost": 37.5,
        "non_metered_tokens": {},
        "priced_token_coverage": 1.0,
        "pricing_fetched_at": {},
        "pricing_sources": ["recorded"],
        "quarterly_cost": 112.5,
        "route_breakdown": {"openai/model [metered]": {
            "cost": 1.25, "entries": 1, "model_requests": 1,
            "model_tool_calls": 0, "model_turns": 1, "tokens": 100,
        }},
        "scope_warnings": [],
        "sessions_count": 1,
        "total_cache_read_tokens": 30,
        "total_cache_write_tokens": 40,
        "total_cost": 1.25,
        "total_input_tokens": 10,
        "total_output_tokens": 20,
        "total_tokens": 100,
        "unique_models": ["model"],
        "unknown_cost_count": 0,
        "unknown_cost_tokens": 0,
        "usage_entries": 1,
        "weekly_cost": 8.75,
        "yearly_cost": 456.25,
    }

    def _run(self):
        usage = app.UsageEntry(
            agent="pi", model_id="model", timestamp="2026-08-01T12:00:00Z",
            input_tokens=10, output_tokens=20, cache_read_tokens=30,
            cache_write_tokens=40, total_tokens=100, cost=1.25,
            cost_breakdown={"total": 1.25}, provider="openai",
            observed_provider="openai", cost_status="recorded", session_id="s1",
            project_id="/repo/a", model_requests=1, model_turns=1,
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output_path = root / "report.json"
            terminal = io.StringIO()
            # Hermetic pricing/preferences: no repo config leaks into the baseline.
            pricing = app.PricingResolver(root / "missing.jsonc", root / "cache")
            prefs = app.PreferencesResolver(root / "missing-prefs.jsonc")
            argv = [
                "eurysx", "--agent", "pi",
                "--from", "2026-08-01", "--to", "2026-08-01",
                "--output", str(output_path),
            ]
            sources = lambda agent, home=None: [
                Source("pi:fake", "fingerprint-1", "1", lambda: [usage])
            ]
            with (
                patch("sys.argv", argv),
                patch.object(app, "collect_sources", side_effect=sources),
                patch.object(app, "get_eurysx_data_dir", return_value=root / "data"),
                patch.object(app, "PricingResolver", return_value=pricing),
                patch.object(app, "PreferencesResolver", return_value=prefs),
                redirect_stdout(terminal),
            ):
                app.main()
            return json.loads(output_path.read_text()), terminal.getvalue()

    def test_json_output_shape_is_the_locked_baseline(self):
        report, _ = self._run()
        self.assertEqual(sorted(report), self.TOP_LEVEL_KEYS)
        stats = report["agent_stats"]["pi"]
        self.assertEqual(sorted(stats), self.AGENT_STATS_KEYS)

    def test_json_values_are_the_locked_baseline(self):
        report, _ = self._run()
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
        self.assertIn("Cache read ratio: 42.9%", terminal)
        self.assertIn("Cache efficiency ratio: 0.8:1", terminal)

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


class VersionTests(unittest.TestCase):
    def test_version_flags_print_the_current_version(self):
        for flag in ("--version", "-v"):
            with self.subTest(flag=flag), patch("sys.argv", ["eurysx", flag]):
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(SystemExit) as exit_code:
                    app.parse_args()

            self.assertEqual(exit_code.exception.code, 0)
            self.assertEqual(output.getvalue().strip(), "eurysx 0.0.4")

    def test_cli_version_matches_package_metadata(self):
        with (Path(__file__).parent / "pyproject.toml").open("rb") as metadata:
            project = tomllib.load(metadata)["project"]

        self.assertEqual(app.__version__, project["version"])


if __name__ == "__main__":
    unittest.main()
