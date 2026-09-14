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

## Key files
- `tests/test_eurysx.py`: single unittest suite and CLI/output baselines.
- `tests/fixtures/`: sanitized source-shape fixtures, including SQL for a temporary OpenCode database.
- `docs/manual.md`: authoritative operational documentation; `README.md` is the concise overview and `docs/cli.md`/`docs/output.md` redirect to the manual.
- `ROADMAP.md`: phase status and planned work.
- `src/eurysx/`: collectors, storage, pricing, analysis, rendering, and CLI implementation.

<!-- opl-init:fp 66c176d929c73997 -->