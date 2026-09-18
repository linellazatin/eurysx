# Repository Guide

## What this is

Eurysx is a local-first usage-observability CLI for AI coding agents. It reads local history from exactly four harnesses: Claude Code, OpenCode, Pi, and Codex, then reports tokens, requests, turns, tool calls, cost, and pricing provenance. Provider-reported aggregates are optional and come only from report files the user saved themselves. Its sole runtime dependency is PyYAML; Python 3.11 or later is required. Collection, storage, analysis, and presentation make no network calls; the only outbound requests are optional pricing-catalog refreshes from `pricing.jsonc` or `--refresh-pricing`, and Eurysx never uploads usage data or reads credentials.

Privacy invariants: never persist or output prompts, responses, file contents, tool arguments, or tool results; never read credentials; never treat unknown pricing as free or infer a price for an unpriced model. A harness-recorded cost takes precedence over policy or guesses.

## Commands

```bash
PYTHONPATH=src python3 -m unittest -v tests/test_eurysx.py
python3 tests/live_test.py --json
python3 -m py_compile src/eurysx/*.py src/eurysx/collectors/*.py src/eurysx/imports/*.py
python3 -m pip install .
eurysx --refresh-pricing
eurysx collect --agent codex
eurysx report --agent codex --days 30 --model x --provider y --billing-mode metered
```

There is no build step beyond installation, and no lint or typecheck configuration. Capture stderr in CLI tests so warnings do not contaminate assertions. `tests/live_test.py` drives a real CLI process inside sandboxed `EURYSX_CONFIG_DIR`/`EURYSX_DATA_DIR`/`EURYSX_CACHE_DIR`/`HOME`, exits `0`/`1`/`2` for pass/fail/bad-invocation, and is documented under `docs/manual.md` -> Running the tests.

## Architecture

The pipeline is discover → collect → normalize → store → price → analyze → present.

- `collectors/` contains one adapter per harness. Adapters enumerate `Source` records and collect metadata only; parser read errors must propagate so failed refreshes are recorded.
- `imports/` holds provider aggregate report readers keyed by the `preferences.jsonc` `type` value, reading user-saved report files through the same `Source` descriptor. Readers return `AggregateImportRow`, never `UsageEntry`, so imported data cannot reach pricing.
- Source granularity is one source per Pi/Codex session, one Claude Code stats-cache source covering transcripts, one whole-database OpenCode source, and one aggregate-import source per declared report glob.
- `store.py` keeps project-local SQLite state in `data/eurysx.db` at `SCHEMA_VERSION = 2` (`sources`, `events`, `aggregate_imports`), replacing each source atomically. A failed refresh retains last-good events and records `last_error`. Imported aggregates register in `sources` under the pseudo-agent `anthropic-aggregate`, and `events()` never reads them, so they cannot reach pricing, attribution, or any local cost lane.
- `models.py` defines normalized usage entries, aggregate import rows, and the shared analysis report. `analysis.py` computes coverage, costs, cache ratios, activity ratios, and the aggregate lane; ratios are `None` when their denominator has no rows.
- `pricing.py` resolves recorded cost, explicit override, configured sources, then unknown, and owns preference/config validation. `render.py` presents terminal, JSON, CSV, Markdown, and HTML output; `cli.py` owns fixed agent mapping, period selectors, and stderr diagnostics.

## Configuration and installation

Repository-local state is in `config/`, `cache/`, and `data/`, which are gitignored, along with report output (`reports/`, `usage-analysis-report-*/`), `research_*`, egg-info, and `docs/superpowers/` (local planning scratch, not authoritative). Optional JSONC configuration starts from `config/pricing.jsonc.sample` and `config/preferences.jsonc.sample`. `EURYSX_CONFIG_DIR`, `EURYSX_CACHE_DIR`, and `EURYSX_DATA_DIR` relocate those directories.

Pricing sources include `amazon-bedrock` (requires `profile` and `region`), `pi-models-store`, `litellm-proxy` (requires explicit `path` and `provider`), and `models-dev` (requires `url`). Preferences support agent defaults and exact-provider overrides only: do not infer routes from model names. Subscription, credit, quota, and local billing modes report incremental USD as `N/A`. The optional `aggregate_imports` array declares saved provider reports (`type`, `path`, `scope`); its rows are organization-level aggregates that never become known cost, coverage, pacing, or comparison figures.

## Testing and operational quirks

Parser changes must bump the matching collector version: Claude Code 1, Codex 2, Pi 3, OpenCode 3; `collectors.PARSER_VERSIONS` mirrors them, and `imports.anthropic_usage.PARSER_VERSION` (currently `"1"`) is the aggregate reader's equivalent. `store.SUPPORTED_SCHEMA_VERSIONS` accepts 0 (fresh), 1, and 2; anything else raises, so a schema change must extend that set with a real migration rather than replacing it. OpenCode turn detection must not depend on SQLite message-row order. Skip malformed live-log lines but propagate whole-source read errors.

`billing_mode` is set during pricing and may become `metered` on recorded-cost conflict; apply `--billing-mode` after pricing, not in SQL. Claude Code stats are aggregate-only, excluded from selected ranges, and stamped with date-only `lastComputedDate`. Keep the JSON baseline shape stable, use explicit JSON `null` ratios, and keep `AGENT_STATS_KEYS` sorted. The JSON report is at `schema_version` 2; `aggregate_imports` always renders, with `reported_cost_usd` `null` rather than `0` when a source carries no reported cost. Validation is lazy in `PreferencesResolver`, so anything that reads `aggregate_imports()` must do so before flushing `warnings`, or the diagnostic is lost.

Release state lives in exactly two places and must agree: `src/eurysx/__init__.py` and `pyproject.toml` (currently 0.2.0); a unit test pins the CLI version string and CI compares the tag to the manifest.

## Live smoke tests

L1-L17 are automated in `tests/live_test.py` under the same ids; run them by hand when a check needs real harness history, a browser, or judgement about the numbers. The script pins `HOME` to an empty directory, so its L1 uses `report` rather than the bare analyze command, and L17 needs `--old-tree` pointing at the previous release's checkout. Run the installed CLI (`eurysx`, not `PYTHONPATH=src`) after any change to `imports/`, `store.py`, `cli.py`, or the presenters; never point the run at this checkout's own `config/` or `data/`, because the store migrates in place on first write and there is no down-migration.

| ID | Condition | Command | Check |
| --- | --- | --- | --- |
| L1 | local store, no imports declared | `eurysx report --days 30` | exits 0; no `PROVIDER-REPORTED AGGREGATES` block; JSON lane rows `0` with `reported_cost_usd` null; the only "aggregate" text is the pre-existing Claude Code period-exclusion warning |
| L2 | no imports declared | `eurysx doctor` | `AGGREGATE IMPORTS` prints `No aggregate imports configured.` |
| L3 | report glob declared in `preferences.jsonc` | `eurysx collect` | `Refreshing declared aggregate imports...` then one `imported N aggregate row(s).` per entry |
| L4 | files untouched since L3 | repeat L3 | every entry reports `unchanged aggregate import.` (fingerprint skip, no re-parse, `ingested_at` stable) |
| L5 | rows stored | `eurysx report --days 30` | lane tokens and `Reported cost (USD)` equal the `sum()` of `aggregate_imports`; usage rows carry no cost and cost rows no tokens; local known cost, coverage, and pacing are unchanged |
| L6 | lane plus local filters | `--agent <other>`, `--model <x>`, `--billing-mode subscription` | the lane keeps its own numbers and prints `Note: --billing-mode filters local usage only; this lane is unfiltered.`; `--model` narrows the lane by design |
| L7 | all exporters | `eurysx report --days 30 --format json\|csv\|markdown\|html --output <path>` | JSON has `schema_version: 2`; CSV has trailing `reported_cost_usd` with `agent=reported_aggregate` rows and `N/A` local cost; Markdown has `## Provider-Reported Aggregates`; HTML shows the lane in `index.html` only, never a per-agent page |
| L8 | a matched file is corrupted | break it, collect, restore | `aggregate import failed (...); last-good rows retained.`, stored row count unchanged, `sources.last_error` set, `doctor` prints `last error:` with `collected` frozen at the last good time |
| L9 | glob matches no files | declare a nonexistent pattern, then report and `doctor` | report warns `has no matching files ...; stored rows are retained as last-good data.`; doctor shows `0 file(s) matched; ...; not collected`; other entries still ingest |
| L10 | invalid entries | declare `"type": "openai-cost-report"` and `"path": 5` | each is dropped with `aggregate import entry ignored: ...` on stderr, exit stays 0, valid entries still run |
| L11 | one glob matches two files holding the same bucket | collect | `contains 1 duplicate bucket(s) with disagreeing values; the first file's value was kept.` (path-order first wins) and the stored count stays deduped; the warning appears only when the fingerprint changes |
| L12 | newest complete bucket older than three days | `eurysx report --days 30` | `aggregate import for <scope> is stale; newest complete bucket is <date> from <file>.` once per source |
| L13 | a pre-0.2.0 store copy | point `EURYSX_DATA_DIR` at a v1 `eurysx.db` and run `report` | `pragma user_version` becomes 2, `aggregate_imports` is created, and every `events` row survives |
| L14 | CLI contract | `--to` without `--from`, `--format` without `--output`, an unknown flag | exit `2` for all three; exit `0` for normal reports; an unknown `--output` extension writes JSON |
| L15 | store holds imported rows and no local events | `eurysx report --days 30`, then `--format html` | renders `No local harness usage stored; reporting provider-reported aggregates only.` plus the lane, writes `index.html` with no per-agent page, exits 0 (HTML must not assume a token leader exists) |
| L16 | no harness history on the machine, imports declared | `eurysx collect` | prints `No local harnesses detected; refreshing declared aggregate imports only.` and ingests the declared reports instead of stopping at `No agents detected.` |
| L17 | a second checkout of the previous release | `python3 tests/live_test.py --old-tree <path>` | every JSON key except `schema_version` and `aggregate_imports` is byte-identical between the two versions on the same store copy |

Two behaviors are intentional, not defects, and predate the aggregate lane: `--format` is never inferred from the `--output` filename, and a `preferences.jsonc` without an `agents` object is ignored with a warning.

## Key files

- `src/eurysx/`: collectors, aggregate imports, storage, pricing, analysis, rendering, and CLI implementation.
- `tests/test_eurysx.py`: single unittest suite and CLI/output baselines.
- `tests/live_test.py`: scripted live checks L1-L17 against a real CLI process in a sandbox.
- `tests/fixtures/`: sanitized source-shape fixtures per harness, SQL for a temporary OpenCode database, LiteLLM price metadata, and `anthropic_usage/` report shapes (documentation-derived, provisional until a live response verifies the key maps).
- `docs/manual.md`: authoritative command, output, configuration, and diagnostics reference; `README.md` is the concise overview and `ROADMAP.md` tracks phase status and planned work.

<!-- opl-init:fp 0efc13cab78bd02e -->
