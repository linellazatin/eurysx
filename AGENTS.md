# Repository Guide

## What this is
Eurysx (v0.0.6, Act III Phases 1-4.1 complete: 1, 2, 3A, 3B, 3C, 3D, 4.1) is a local-first usage-observability CLI for AI coding agents. It reads local history from exactly four harnesses — Claude Code, OpenCode, Pi, Codex — and reports tokens, requests, turns, tool calls, cost, and pricing provenance. Hard privacy invariants: never persist or output prompts, responses, file contents, tool arguments, or tool results; never read credentials; never present unknown pricing as free or guess a price for an unpriced model. Recorded harness cost always wins over any policy or guess. Zero runtime dependencies, Python >=3.11.

## Commands
```bash
PYTHONPATH=src python3 -m unittest -v test_eurysx.py   # tests (no pytest; single file, 82 tests; capture stderr in CLI tests to keep warnings out of the log)
python3 -m py_compile src/eurysx/*.py                  # syntax check
python3 -m pip install .                               # installs `eurysx` entry point
eurysx --refresh-pricing                               # force pricing cache refresh
eurysx collect --agent codex                           # store metadata only
eurysx report --agent codex --days 30 --model x --provider y --billing-mode metered
```
No build step beyond pip install, no lint, no typecheck configured.

## Architecture
Pipeline: discover → collect → normalize → store → price → analyze → present. Modules under `src/eurysx/`:
- `collectors/` (one adapter per harness + `paths.py` + `sources.py`): locate and normalize local metadata only. Each adapter's `enumerate_sources(home)` yields `Source(key, fingerprint, parser_version, parse)`; fingerprints are stat digests (size + mtime_ns), and the CLI skips sources whose fingerprint and parser version match the store. Parse closures must raise on read errors so the CLI can record failures. No pricing, storage, analysis, or rendering here.
- Per-harness source granularity: one source per Pi/Codex session file; Claude Code is one stats-cache source whose fingerprint also covers all transcripts (no per-project breakdown exists); OpenCode is one whole-DB source.
- `store.py`: project-local SQLite (`data/eurysx.db`), per-source atomic replace, last-good events retained with `last_error` recorded on refresh failure (a stderr warning). `events()` now filters agent, date range, model and provider in SQL (Python date filter kept as the equivalence reference); NULL provider matches `--provider unknown` via COALESCE. Indices on provider/model/project/session are additive and idempotent. Decimal-text recorded costs, per-event `project_id`/`session_id` attribution.
- `models.py`: `UsageEntry` (normalized usage + provenance, no content fields) and `AnalysisReport` (period, per-agent `AgentStats`, `AgentDisplay` periods, pricing/preferences provenance) — the single structured result every presenter consumes.
- `analysis.py`: `UsageAnalyzer.analyze_agent(..., billing_modes=...)` → `AgentStats`; `display_period` pins all-time mode to first usage date. Coverage/cost math, cache ratios, and the Phase 4.1 activity ratios (`requests_per_turn`, `tool_calls_per_request`, `tool_calls_per_turn`) live here. Ratio fields are `None` when the denominator has no rows — never 0.0, and JSON keeps them as explicit `null`.
- `pricing.py`: resolution order is recorded cost → explicit override → route primary source → ordered `otherSources` → unknown. Cache in `cache/pricing-<source>.json`; on refresh failure falls back to valid cache with a warning.
- `render.py`: all presentation — `print_single_agent_report`, `print_summary_comparison`, `build_json_report`. `period_comparison` (current vs same-length previous window, `_previous_window`) renders as terminal `PERIOD COMPARISON` plus an additive JSON block; omitted for all-time runs. `project_breakdown`/`session_breakdown` reuse the `model_breakdown` shape with an `unknown` bucket.
- `cli.py`: fixed agent mapping; period selectors (`--days/--weeks/--from/--month/--quarter/--year/--ytd`) are mutually exclusive. Default and `report` both read back from the store. All diagnostics go to stderr (resolver/preference warnings, per-source refresh failures, and the read-only store-quality warnings in `_warn_store_quality`: sources still on an older parser version, and sources whose files no longer exist).

## Configuration and installation
Checkout-local only (no XDG/App Support): `config/pricing.jsonc`, `config/preferences.jsonc`, `cache/`, `data/`. All gitignored; create configs from tracked `.sample` templates. Both are JSONC (comments, trailing commas) and optional. `EURYSX_CONFIG_DIR`, `EURYSX_CACHE_DIR`, `EURYSX_DATA_DIR` exist only for deliberate relocation. Price sources: `amazon-bedrock` (needs `profile` + `region`), `pi-models-store`, `models-dev` (needs `url`). Preferences use agent-level defaults plus exact-provider overrides; no model-match rules; routes are never inferred from model names. Billing modes `subscription|credit|quota|local` report `N/A` incremental USD.

## Testing and operational quirks
- `tests/fixtures/` holds sanitized fixtures verifying known file shapes, not every live release. The OpenCode fixture is a `.sql` file that builds a temporary SQLite database — never commit a binary `.db` fixture. Pi and Codex fixtures include `cwd` header metadata for attribution parity; the Pi fixture is live-shaped (session id on the `type: "session"` header only, no per-event `sessionId`).
- Parser-behavior changes must bump that collector's `PARSER_VERSION` so stored events re-collect: Claude Code 1, Codex 2, Pi 3 (header session id), OpenCode 3 (ISO-8601 timestamps; epoch-millis rows re-collect). `collectors.PARSER_VERSIONS` mirrors them for the CLI's drift warning, so a bump needs no CLI change. `collectors.PARSER_VERSIONS` mirrors those for the drift warning, so a bump needs no CLI change — but note `report` never re-normalizes: stored rows are only rewritten by `collect`/default, and until then the lexicographic SQL period filter silently drops non-ISO timestamps.
- OpenCode turn detection must stay independent of SQLite message-row order; malformed lines inside a live log are skipped, whole-source read errors propagate.
- `billing_mode` is a pricing-time artifact flipped to `metered` on recorded-cost conflict, so `--billing-mode` is applied post-pricing in the analyzer, never in SQL.
- Claude Code's stats cache is aggregate-only: excluded from selected date ranges, with the scope warning driven by a store presence check so it survives SQL filtering. Its rows are stamped with `lastComputedDate` (date-only `YYYY-MM-DD`), which `extract_date_from_timestamp` must keep parsing or all-time Claude Code rates collapse to zero; aggregate rows stay out of `daily_activity` and the terminal prints `n/a` per-active-day.
- `Act3Phase1BaselineTests` locks the JSON `--output` shape/values and terminal section headers against hermetic resolvers. Additive keys (`project_breakdown`, `session_breakdown`, `period_comparison`, the three ratio keys) were extended into it; `AGENT_STATS_KEYS` is compared against a sorted list, so keep it in Python's sort order (note `tool_*` sorts after `total_*`). The shape stays explicitly unstable until Act III Phase 6 pins it. Keep it passing.
- `sessions_count` counts attributed sessions only, while `session_breakdown` keeps an `unknown` bucket; the terminal labels that difference rather than reconciling the numbers. Attribution is per collector: Codex reads its `session_meta` payload id, Pi its `type: "session"` header, OpenCode its session rows; Claude Code has none (aggregate-only), so its sessions always land in `unknown`.
- Test namespace convention: tests import `eurysx.cli as app` and `eurysx.models as models`; the CLI does not re-export model classes.
- Version bumps touch six places: `pyproject.toml`, `src/eurysx/__init__.py`, the hardcoded `"eurysx 0.0.6"` in `VersionTests`, `CHANGELOG.md`, the `"v0.0.x is in development"` line in `README.md`, and the `"Applies to Eurysx v0.0.x"` header in both `docs/cli.md` and `docs/output.md`. A test asserts CLI and package metadata versions match.
- Docs convention: each Act III phase updates README, CHANGELOG, and `docs/` references, then is re-assessed. `docs/cli.md` and `docs/output.md` are the user manual today; README stays authoritative for the JSONC schema until Act III Phase 8 lands `docs/manual.md` as the operational source of truth for developers and agents (README then keeps only the concise overview, and those two pages fold into the manual). Git commits, tags, and releases are user-owned operations.
- `reports/`, `eur-test*` outputs, and the local store are disposable during development and ignored by Git.

<!-- opl-init:fp b593ad88c642e50e -->
