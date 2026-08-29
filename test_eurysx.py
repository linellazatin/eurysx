import json
import io
import sqlite3
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

import eurysx as app


FIXTURES = Path(__file__).parent / "tests" / "fixtures"


class CollectorFixtureTests(unittest.TestCase):
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

            with patch.object(app.Path, "home", return_value=root):
                usages = app.ClaudeCodeExtractor.extract_usage()

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
        usages = app.CodexExtractor.extract_usage_from_session(
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

            with patch.object(app.Path, "home", return_value=root):
                usages = app.PiAgentExtractor.extract_usage()

        usage = next(item for item in usages if not item.is_metric_only)
        self.assertEqual(usage.model_id, "gpt-5.6")
        self.assertEqual(usage.provider, "openai")
        self.assertEqual(usage.timestamp, "2026-08-01T13:00:00Z")
        self.assertEqual(usage.session_id, "pi-session-1")
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

            with patch.object(app.Path, "home", return_value=root):
                usages = app.OpenCodeExtractor.extract_usage()

        usage = next(item for item in usages if not item.is_metric_only)
        self.assertEqual(usage.model_id, "gpt-5.6")
        self.assertEqual(usage.provider, "openai")
        self.assertEqual(usage.timestamp, "1754056800000")
        self.assertEqual(usage.session_id, "opencode-session-1")
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

            with patch.object(app.Path, "home", return_value=root):
                usages = app.OpenCodeExtractor.extract_usage()

        self.assertEqual(sum(item.model_turns for item in usages), 1)

    @staticmethod
    def _write_opencode_fixture(db_path, assistant_first=False):
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


class PricingTests(unittest.TestCase):
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
            with patch.object(app.Path, "home", return_value=root):
                resolver = app.PricingResolver(config, root / "cache")
            result = resolver.resolve("provider", "model")
            self.assertEqual(result["source"], "pi-models-store")
            self.assertEqual(result["pricing"]["output"], 2)
            self.assertTrue((root / "cache" / "pricing-pi-models-store.json").exists())

    @patch("eurysx.subprocess.run")
    def test_aws_pricing_uses_configured_profile_and_region(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps({"PriceList": []})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "pricing.jsonc"
            config.write_text(json.dumps({"sources": {
                "aws-bedrock": {"enabled": True, "profile": "p", "region": "r"}
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
                "schema_version": 1, "source": "models-dev",
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

    def test_terminal_labels_cost_as_known_when_pricing_is_incomplete(self):
        usages = [self._usage("configured", 1.5, 100), self._usage("unknown", 0.0, 100)]
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", usages,
            date(2026, 8, 1), date(2026, 8, 1), "1d",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            app.print_single_agent_report(
                "pi", usages, stats, date(2026, 8, 1), date(2026, 8, 1), "1d"
            )

        self.assertIn("KNOWN COST", output.getvalue())
        self.assertIn("Metered token coverage:", output.getvalue())

    def test_zero_token_report_marks_pricing_coverage_not_applicable(self):
        stats = app.UsageAnalyzer.analyze_agent(
            "pi", [self._usage("recorded", 0.0, 0)], date(2026, 8, 1), date(2026, 8, 1), "1d",
        )
        output = io.StringIO()
        with redirect_stdout(output):
            app.print_single_agent_report(
                "pi", [self._usage("recorded", 0.0, 0)], stats,
                date(2026, 8, 1), date(2026, 8, 1), "1d"
            )

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
            app.print_single_agent_report(
                "pi", [metered, subscription, unknown], stats,
                date(2026, 8, 1), date(2026, 8, 1), "1d"
            )

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


class PreferencesTests(unittest.TestCase):
    @staticmethod
    def _usage(provider="openai", model="gpt-5.6"):
        return app.UsageEntry(
            agent="codex", model_id=model, timestamp="2026-08-01T12:00:00Z",
            input_tokens=10, output_tokens=0, cache_read_tokens=0,
            cache_write_tokens=0, total_tokens=10, cost=0.0,
            cost_breakdown={}, provider=provider,
        )

    def test_preferences_choose_the_most_specific_route_rule(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "preferences.jsonc"
            path.write_text(json.dumps({"agents": {
                "claude-code": {"default": {"billingMode": "unknown"}, "routes": []},
                "codex": {
                    "default": {"billingMode": "unknown"},
                    "routes": [
                        {"match": {"provider": "openai"}, "set": {"billingMode": "subscription"}},
                        {"match": {"provider": "openai", "model": "gpt-5.6"}, "set": {
                            "provider": "amazon-bedrock", "billingMode": "metered",
                            "pricingProvider": "amazon-bedrock", "pricingModel": "bedrock.gpt-5.6",
                        }},
                    ],
                },
                "opencode": {"default": {"billingMode": "unknown"}, "routes": []},
                "pi": {"default": {"billingMode": "unknown"}, "routes": []},
            }}))
            preferences = app.PreferencesResolver(path)
            usage = self._usage()
            preferences.apply(usage)

        self.assertEqual(usage.observed_provider, "openai")
        self.assertEqual(usage.provider, "amazon-bedrock")
        self.assertEqual(usage.billing_mode, "metered")
        self.assertEqual(usage.pricing_provider, "amazon-bedrock")
        self.assertEqual(usage.pricing_model, "bedrock.gpt-5.6")

    def test_subscription_usage_is_not_priced_even_when_price_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            preferences_path = root / "preferences.jsonc"
            preferences_path.write_text(json.dumps({"agents": {
                "claude-code": {"default": {"billingMode": "unknown"}, "routes": []},
                "codex": {"default": {"billingMode": "subscription"}, "routes": []},
                "opencode": {"default": {"billingMode": "unknown"}, "routes": []},
                "pi": {"default": {"billingMode": "unknown"}, "routes": []},
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

    def test_conflicting_route_rules_emit_a_diagnostic_and_use_the_first(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "preferences.jsonc"
            path.write_text(json.dumps({"agents": {
                "claude-code": {"default": {"billingMode": "unknown"}, "routes": []},
                "codex": {"default": {"billingMode": "unknown"}, "routes": [
                    {"match": {"provider": "openai"}, "set": {"billingMode": "subscription"}},
                    {"match": {"provider": "openai"}, "set": {"billingMode": "local"}},
                ]},
                "opencode": {"default": {"billingMode": "unknown"}, "routes": []},
                "pi": {"default": {"billingMode": "unknown"}, "routes": []},
            }}))
            preferences = app.PreferencesResolver(path)
            usage = self._usage()
            preferences.apply(usage)

        self.assertEqual(usage.billing_mode, "subscription")
        self.assertTrue(preferences.warnings)


class DateRangeTests(unittest.TestCase):
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

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            resolver = app.PricingResolver(root / "missing.jsonc", root / "cache")
            patches = (
                patch.object(app.AgentPaths, "detect_agents", return_value=list(agents)),
                patch.object(app.ClaudeCodeExtractor, "extract_usage", return_value=entries("claude-code")),
                patch.object(app.OpenCodeExtractor, "extract_usage", return_value=entries("opencode")),
                patch.object(app.PiAgentExtractor, "extract_usage", return_value=entries("pi")),
                patch.object(app.CodexExtractor, "extract_usage", return_value=entries("codex")),
                patch.object(app, "PricingResolver", return_value=resolver),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
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
            ]), patch.object(app.ClaudeCodeExtractor, "extract_usage", return_value=[aggregate]), \
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


class PricingPathTests(unittest.TestCase):
    def test_defaults_are_local_to_the_eurysx_checkout(self):
        root = Path("/workspace/eurysx")

        self.assertEqual(
            app.get_eurysx_dirs(environ={}, root=root),
            (root / "config", root / "cache"),
        )

    def test_environment_overrides_win_without_creating_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_dir = root / "config-override"
            cache_dir = root / "cache-override"
            config, cache = app.get_eurysx_dirs(
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
            with patch.object(app, "get_eurysx_dirs", return_value=(config_dir, cache_dir)):
                resolver = app.PricingResolver()

            self.assertEqual(resolver.config_path, config_dir / "pricing.jsonc")
            self.assertEqual(resolver.cache_dir, cache_dir)
            self.assertFalse(config_dir.exists())
            self.assertFalse(cache_dir.exists())


class SummaryOutputTests(unittest.TestCase):
    def test_comparison_labels_cost_as_known(self):
        stats = app.AgentStats(agent="pi", usage_entries=1, total_tokens=100,
                               total_cost=1.5, known_cost=1.5)
        output = io.StringIO()
        with redirect_stdout(output):
            app.print_summary_comparison({"pi": stats})

        self.assertIn("Known Cost", output.getvalue())


class VersionTests(unittest.TestCase):
    def test_version_flags_print_the_current_version(self):
        for flag in ("--version", "-v"):
            with self.subTest(flag=flag), patch("sys.argv", ["eurysx", flag]):
                output = io.StringIO()
                with redirect_stdout(output), self.assertRaises(SystemExit) as exit_code:
                    app.parse_args()

            self.assertEqual(exit_code.exception.code, 0)
            self.assertEqual(output.getvalue().strip(), "eurysx 0.0.1")

    def test_cli_version_matches_package_metadata(self):
        with (Path(__file__).parent / "pyproject.toml").open("rb") as metadata:
            project = tomllib.load(metadata)["project"]

        self.assertEqual(app.__version__, project["version"])


if __name__ == "__main__":
    unittest.main()
