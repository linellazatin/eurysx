# Repository Guide

## What this is
Eurysx (v0.0.4, Act III Phases 1-2 complete) is a local-first usage-observability CLI for AI coding agents. It reads local history from exactly four harnesses — Claude Code, OpenCode, Pi, Codex — and reports tokens, requests, turns, tool calls, cost, and pricing provenance. Hard privacy invariants: never persist or output prompts, responses, file contents, tool arguments, or tool results; never read credentials; never present unknown pricing as free or guess a price for an unpriced model. Recorded harness cost always wins over any policy or source. Zero runtime dependencies, Python >=3.11.

## Commands
```bash
PYTHONPATH=src python3 -m unittest -v test_eurysx.py   # tests (no pytest; single file, 59 tests)
python3 -m py_compile src/eurysx/*.py                  # syntax check
python3 -m pip install .                               # installs `eurysx` entry point
eurysx --refresh-pricing                               # force pricing cache refresh
eurysx collect --agent codex                           # store metadata only
eurysx report --agent codex --days 30                  # read store, no collection
```
No build step beyond pip install, no lint, no typecheck configured.

## Architecture
Pipeline: discover → collect → normalize → store → price → analyze → present. Modules under `src/eurysx/`:
- `collectors/` (one adapter per harness + `paths.py`): locate and normalize local metadata only. No pricing, storage, analysis, or rendering here. Each adapter's `enumerate_sources(home)` yields `Source(key, fingerprint, parser_version, parse)` (`collectors/sources.py`); fingerprints are stat digests (size + mtime_ns), and the CLI skips sources whose fingerprint and parser version match the store. Parse closures must raise on read errors so the CLI can record failures.
- Per-harness source granularity: one source per Pi/Codex session file; Claude Code is one stats-cache source whose fingerprint also covers all transcripts (it has no per-project breakdown); OpenCode is one whole-DB source.
- `store.py`: project-local SQLite (`data/eurysx.db` relative to cwd), per-source atomic replace, last-good events retained and `last_error` recorded on refresh failure (surfaced as a stderr warning), decimal-text recorded costs, per-event `project_id` attribution. `events()` filters by agent only today; date/model/provider filtering in SQL is Act III Phase 3A/3B work.
- `models.py`: `UsageEntry` carries normalized usage plus provenance; no content fields. `AnalysisReport` is the single structured analysis result (period, per-agent `AgentStats`, per-agent `AgentDisplay` display periods, pricing/preferences provenance) that every presenter consumes. `AgentStats` carries the cache-read and cache-efficiency ratios computed in `analysis.py`.
- `analysis.py`: `UsageAnalyzer.analyze_agent` → `AgentStats`; `display_period` derives per-agent display periods (all-time mode pins to first usage date). Coverage/cost math lives here.
- `pricing.py`: resolution order is recorded cost → explicit override → route primary source → ordered `otherSources` → unknown. Cache in `cache/pricing-<source>.json`; on refresh failure falls back to valid cache with a warning.
- `render.py`: all presentation. Terminal presenters `print_single_agent_report(report, agent)` and `print_summary_comparison(report)`, plus JSON `build_json_report(report)` / `_agent_stats_dict`, consume only `AnalysisReport` — the CLI never assembles presentation payloads.
- `cli.py`: fixed agent mapping; period selectors (`--days/--weeks/--from/--month/--quarter/--year/--ytd`) are mutually exclusive. Default and `report` both read back from the store.

## Configuration and installation
Checkout-local only (no XDG/App Support): `config/pricing.jsonc`, `config/preferences.jsonc`, `cache/`, `data/`. All gitignored; create configs from tracked `.sample` templates. Both are JSONC (comments, trailing commas) and optional. `EURYSX_CONFIG_DIR`, `EURYSX_CACHE_DIR`, `EURYSX_DATA_DIR` exist only for deliberate relocation. Price sources: `amazon-bedrock` (needs `profile` + `region`), `pi-models-store`, `models-dev` (needs `url`). Preferences use agent-level defaults plus exact-provider overrides; no model-match rules; routes are never inferred from model names. Billing modes `subscription|credit|quota|local` report `N/A` incremental USD.

## Testing and operational quirks
- `tests/fixtures/` holds sanitized fixtures; they verify known file shapes, not every live harness release. The OpenCode fixture is a `.sql` file that builds a temporary SQLite database — never commit a binary `.db` fixture. Pi and Codex fixtures include `cwd` header metadata for attribution parity.
- Parser-behavior changes (e.g. new fields) must bump that collector's `PARSER_VERSION` so stored events re-collect; Pi/Codex/OpenCode are at 2.
- OpenCode turn detection must stay independent of SQLite message-row order; malformed lines inside a live log are skipped, but whole-source read errors propagate.
- Version bumps touch five places: `pyproject.toml`, `src/eurysx/__init__.py`, a hardcoded `"eurysx 0.0.4"` string in `VersionTests`, `CHANGELOG.md`, and the `"v0.0.x is in development"` line in `README.md`. A test asserts CLI and package metadata versions match.
- Claude Code's stats cache is aggregate-only: excluded from selected date ranges with a scope warning.
- `Act3Phase1BaselineTests` locks the JSON `--output` shape and values plus the terminal report's section headers against hermetic pricing/preferences resolvers. It is the pre-refactor baseline Act III later phases diff against and must keep passing unchanged through refactors (Phase 2 proved this).
- Test namespace convention: tests import `eurysx.cli as app` for CLI-owned names and `eurysx.models as models` for model classes; the CLI no longer re-exports model classes for tests.
- Docs convention: each Act III phase lands, updates README, CHANGELOG, and the `docs/` reference files, and is re-assessed before the next starts; version bumps land on user request. `docs/cli.md` and `docs/output.md` are the user manual (README links them and stays authoritative for the JSONC schema). Git commits, tags, and releases are user-owned operations.
- `reports/` and `eur-test*` outputs are ignored by Git; the local store is disposable during development.

## Key files
- `src/eurysx/cli.py` — CLI contract and store-refresh loop; `src/eurysx/pricing.py` — largest logic surface; `src/eurysx/render.py` — all presenters (terminal + JSON)
- `src/eurysx/collectors/` — per-harness adapters (the compatibility surface)
- `test_eurysx.py` — the entire test suite
- `docs/cli.md`, `docs/output.md` — user-facing command and report-output manual
- `README.md` — user-facing config contract (authoritative for JSONC schema)
- `ROADMAP.md` / `CHANGELOG.md` — Act structure and shipped states

<!-- opl-init:fp 9e866c9bf1f26b3b -->