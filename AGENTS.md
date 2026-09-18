# Repository Guide

## What this is

Eurysx is a local-first usage-observability CLI for AI coding agents. It reads local history from exactly four harnesses: Claude Code, OpenCode, Pi, and Codex, then reports tokens, requests, turns, tool calls, cost, and pricing provenance. Runtime dependencies are none; Python 3.11 or later is required.

Privacy invariants: never persist or output prompts, responses, file contents, tool arguments, or tool results; never read credentials; never treat unknown pricing as free or infer a price for an unpriced model. A harness-recorded cost takes precedence over policy or guesses.

## Commands

```bash
PYTHONPATH=src python3 -m unittest -v tests/test_eurysx.py
python3 -m py_compile src/eurysx/*.py
python3 -m pip install .
eurysx --refresh-pricing
eurysx collect --agent codex
eurysx report --agent codex --days 30 --model x --provider y --billing-mode metered
```

There is no build step beyond installation, and no lint or typecheck configuration. Capture stderr in CLI tests so warnings do not contaminate assertions.

## Architecture

The pipeline is discover → collect → normalize → store → price → analyze → present.

- `collectors/` contains one adapter per harness. Adapters enumerate `Source` records and collect metadata only; parser read errors must propagate so failed refreshes are recorded.
- Source granularity is one source per Pi/Codex session, one Claude Code stats-cache source covering transcripts, and one whole-database OpenCode source.
- `store.py` keeps project-local SQLite state in `data/eurysx.db`, replacing each source atomically. A failed refresh retains last-good events and records `last_error`.
- `models.py` defines normalized usage entries and the shared analysis report. `analysis.py` computes coverage, costs, cache ratios, and activity ratios; ratios are `None` when their denominator has no rows.
- `pricing.py` resolves recorded cost, explicit override, configured sources, then unknown. `render.py` presents terminal and JSON output; `cli.py` owns fixed agent mapping, period selectors, and stderr diagnostics.

## Configuration and installation

Repository-local state is in `config/`, `cache/`, and `data/`, which are gitignored. Optional JSONC configuration starts from `config/pricing.jsonc.sample` and `config/preferences.jsonc.sample`. `EURYSX_CONFIG_DIR`, `EURYSX_CACHE_DIR`, and `EURYSX_DATA_DIR` relocate those directories.

Pricing sources include `amazon-bedrock` (requires `profile` and `region`), `pi-models-store`, and `models-dev` (requires `url`). Preferences support agent defaults and exact-provider overrides only: do not infer routes from model names. Subscription, credit, quota, and local billing modes report incremental USD as `N/A`.

## Testing and operational quirks

Parser changes must bump the matching collector version: Claude Code 1, Codex 2, Pi 3, OpenCode 3; `collectors.PARSER_VERSIONS` mirrors them. OpenCode turn detection must not depend on SQLite message-row order. Skip malformed live-log lines but propagate whole-source read errors.

`billing_mode` is set during pricing and may become `metered` on recorded-cost conflict; apply `--billing-mode` after pricing, not in SQL. Claude Code stats are aggregate-only, excluded from selected ranges, and stamped with date-only `lastComputedDate`. Keep JSON baseline shape stable, use explicit JSON `null` ratios, and keep `AGENT_STATS_KEYS` sorted.

## Live smoke tests

Run these against the installed CLI (`eurysx`, not `PYTHONPATH=src`) after any change to `imports/`, `store.py`, `cli.py`, or the presenters. Always point `EURYSX_CONFIG_DIR` and `EURYSX_DATA_DIR` at throwaway directories: the store is migrated in place on first write and there is no down-migration, and the repository's own `config/` and `data/` must not absorb test rows.

| ID | Condition | Command | Check |
| --- | --- | --- | --- |
| L1 | real store, no imports declared | `eurysx --agent all --days 30` | exits 0; no `PROVIDER-REPORTED AGGREGATES` block; the only "aggregate" text is the pre-existing Claude Code period-exclusion warning |
| L2 | no imports declared | `eurysx doctor` | `AGGREGATE IMPORTS` prints `No aggregate imports configured.` |
| L3 | report glob declared in `preferences.jsonc` | `eurysx collect --agent pi` | `Refreshing declared aggregate imports...` then one `imported N aggregate row(s).` per entry |
| L4 | files untouched since L3 | repeat L3 | every entry reports `unchanged aggregate import.` (fingerprint skip, no re-parse) |
| L5 | rows stored | `eurysx report --days 30` | lane tokens and `Reported cost (USD)` equal `select sum(...), sum(cost_usd) from aggregate_imports`; local known cost, coverage, and pacing are unchanged |
| L6 | lane plus local filters | `eurysx report --agent <other> --days 30`, `--model <x>`, `--billing-mode subscription` | the lane keeps its own numbers and prints `Note: --billing-mode filters local usage only; this lane is unfiltered.`; `--model` narrows the lane by design |
| L7 | all exporters | `eurysx report --days 30 --format json\|csv\|markdown\|html --output <path>` | JSON has `schema_version: 2` and an always-present `aggregate_imports` whose cost is `null`, never `0`; CSV has trailing `reported_cost_usd` with `agent=reported_aggregate` rows and `N/A` local cost; Markdown has `## Provider-Reported Aggregates`; HTML shows the lane in `index.html` only, never a per-agent page. `--format` is not inferred from the filename |
| L8 | a matched file is corrupted | `printf '{oops' > <file>`, then L3, then restore | `aggregate import failed (...); last-good rows retained.`, stored row count unchanged, `sources.last_error` set, `doctor` prints `last error:` with `collected` frozen at the last good time |
| L9 | glob matches no files | declare a nonexistent pattern, then report and `eurysx doctor` | report warns `has no matching files ...; stored rows are retained as last-good data.`; doctor shows `0 file(s) matched; ...; not collected`; other entries still ingest |
| L10 | invalid entries | declare `"type": "openai-cost-report"` and `"path": 5` | each is dropped with `aggregate import entry ignored: ...`, exit stays 0, valid entries still run |
| L11 | one glob matches two files holding the same bucket | collect | `contains N duplicate bucket(s) with disagreeing values; the first file's value was kept.` and the stored count stays deduped; the warning appears only when the fingerprint changes |
| L12 | newest complete bucket older than three days | `eurysx report --days 30` | `aggregate import for <scope> is stale; newest complete bucket is <date> from <file>.` once per source |
| L13 | a pre-0.1.5 store copy | point `EURYSX_DATA_DIR` at a v1 `eurysx.db`, run `report`, and compare JSON with the same run under 0.1.4 | `pragma user_version` becomes 2, `events` count unchanged, and every JSON key except `schema_version` and `aggregate_imports` is byte-identical |
| L14 | CLI contract | `--to` without `--from`, `--format` without `--output`, an unknown flag | exit `2` for all three; exit `0` for normal reports; an unknown `--output` extension silently writes JSON |
| L15 | store holds imported rows and no local events | `eurysx report --days 30`, then the same with `--format html` | renders `No local harness usage stored; reporting provider-reported aggregates only.` plus the lane, writes `index.html` with no per-agent page, and exits 0 (HTML must not assume a token leader exists) |
| L16 | no harness history on the machine, imports declared | `eurysx collect` | prints `No local harnesses detected; refreshing declared aggregate imports only.` and ingests the declared reports instead of stopping at `No agents detected.` |

Two behaviors are intentional, not defects, and predate the aggregate lane: `--format` is never inferred from the `--output` filename, and a `preferences.jsonc` without an `agents` object is ignored with a warning.

## Key files

- `tests/test_eurysx.py`: single unittest suite and CLI/output baselines.
- `tests/fixtures/`: sanitized source-shape fixtures, including SQL for a temporary OpenCode database.
- `docs/manual.md`: authoritative operational documentation; `README.md` is the concise overview and `docs/cli.md`/`docs/output.md` redirect to the manual.
- `ROADMAP.md`: phase status and planned work.
- `src/eurysx/`: collectors, storage, pricing, analysis, rendering, and CLI implementation.

<!-- opl-init:fp aabb852182caecac -->
