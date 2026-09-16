# Eurysx operational manual

Eurysx is a local-first CLI for metadata-only usage analysis of Claude Code, OpenCode, Pi, and Codex. It never persists prompts, responses, file content, tool arguments, tool results, or credentials.

## Install and first run

```bash
pip3 install eurysx
eurysx --help
eurysx doctor
eurysx
```

Development checks:

```bash
PYTHONPATH=src python3 -m unittest -v tests/test_eurysx.py
python3 -m py_compile src/eurysx/*.py src/eurysx/collectors/*.py
```

## Commands

| Command | Behavior |
| --- | --- |
| `eurysx` | Collect detected harness metadata, then report it. |
| `eurysx collect` | Collect and store metadata only. |
| `eurysx report` | Read stored metadata without collection. |
| `eurysx doctor` | Read-only harness, source, cache, and configuration diagnostics. |

`doctor` does not parse sessions, fetch pricing, write cache files, or delete retained data.

## Selectors and exports

`--agent` accepts `claude-code`, `opencode`, `pi`, `codex`, or `all`. `all` cannot be combined with named agents. Period selectors are mutually exclusive: `--days`, `--weeks`, `--from` with optional `--to`, `--month`, `--quarter`, `--year`, and `--ytd`. `--model`, `--provider`, and `--billing-mode` combine with agent and period filters.

```bash
eurysx --agent codex --days 30
eurysx report --agent pi --provider unknown --month 2026-08
eurysx --agent all --output reports/usage.csv --format csv
eurysx --agent all --output reports/usage.md --format markdown
eurysx --agent all --days 30 --format html
eurysx --agent all --days 30 --output reports/august-usage --format html
```

`--output PATH` writes a file. `--format json|csv|markdown|html` selects its format and defaults to JSON. CSV and Markdown require `--output`. HTML writes a directory: without `--output`, its name is `usage-analysis-report-YYYYMMDD-HHMMSS`; with `--output`, PATH is the new destination directory and must not already exist.

JSON schema version 1 is stable. It contains `schema_version: 1`, analysis period, pricing/preferences provenance, agent stats, `unresolved_routes`, `pacing`, and period comparison. Aggregate breakdowns retain cost-status counts so a numeric cost is never inferred from unavailable pricing.

| Output | Best for | Contents and interaction |
| --- | --- | --- |
| Terminal | Immediate local inspection | Full per-agent analysis, comparison summary, and token leaders; no saved artifact or interaction. |
| JSON | Automation and stable integrations | The complete versioned analysis contract, including additive comparison leaders. |
| CSV | Spreadsheet or tabular processing | Deterministic agent/provider/model route rows with `cost_status`; unavailable `known_cost_usd` is `N/A`. It deliberately has no summary rows. |
| Markdown | Human-readable saved summaries | Token leaders, period, agent summaries, and route tables with the same cost status. |
| HTML | Offline exploration of one report | A self-contained bundle with overview and per-agent pages. The overview shows leader routes per harness and the combined top three. Agent pages retain the richer analysis sections, disclose supporting detail on demand, show cost context, and provide sortable breakdowns. Wide sortable tables scroll inside their section on narrow screens. |

HTML writes `index.html` plus one page per analyzed agent. Total usage is initially open; model, project, session, daily activity, route, and unresolved-route tables sort locally by clicking a column heading. Metric, projection, and combined-summary table order stays fixed. Every page has sidebar navigation, all content remains local, and no server or network request is made.

## Local state lifecycle

Default paths are relative to the current working directory:

```text
config/pricing.jsonc
config/preferences.jsonc
cache/pricing-<source>.json
data/eurysx.db
```

`EURYSX_CONFIG_DIR`, `EURYSX_CACHE_DIR`, and `EURYSX_DATA_DIR` relocate them. Cache files are disposable pricing metadata. The SQLite store retains last-good events when source parsing fails or source files disappear; `report` never deletes or re-normalizes rows. Run `eurysx collect` after a parser-version warning.

## Collectors

| Harness | Source granularity | Attribution / limit |
| --- | --- | --- |
| Claude Code | stats cache plus transcript metadata | aggregate stats are excluded from bounded ranges. |
| OpenCode | one database | project when session directory exists. |
| Pi | one session file | session header and working directory when present. |
| Codex | one session file | session and working directory when present. |

Collectors normalize usage metadata only. Parser read failures propagate to collection, where last-good store data is retained.

## Pricing, preferences, and budgets

Pricing precedence is recorded harness cost, explicit override, configured source order, then unknown. Unknown is never free. Exact provider/model matching is required; aliases are provider-scoped. Supported sources are `amazon-bedrock`, `pi-models-store`, and `models-dev`.

Preferences define agent defaults, exact provider overrides, and optional provider-scoped `modelIdRules`. A rule has exactly one literal `exact` or `prefix` matcher; exact wins, then the longest prefix. Invalid or duplicate exact rules warn and fall back to the provider policy. A rule may override `provider`, `billingMode`, `pricing`, and `budget`. Billing modes are `metered`, `subscription`, `credit`, `quota`, `local`, and `unknown`. Subscription-like modes report `N/A` incremental USD.

```jsonc
{
  "schemaVersion": 3,
  "agents": {
    "codex": {
      "budget": { "usd": 100, "period": "month" },
      "providers": {
        "openai": { "billingMode": "subscription" },
        "litellm": {
          "billingMode": "unknown",
          "modelIdRules": [{ "prefix": "local.", "billingMode": "local" }]
        },
        "amazon-bedrock": {
          "billingMode": "metered",
          "budget": { "usd": 25, "period": "week" },
          "pricing": { "source": "amazon-bedrock" }
        }
      }
    }
  }
}
```

A model-ID rule has one literal `exact` or `prefix` matcher and may override `provider`, `billingMode`, `pricing`, and `budget`; exact wins over prefix, then the longest prefix wins. Rules never discover a proxy upstream provider. Route exports retain both effective provider and observed proxy provider. Rule budgets pace independently from their parent provider budget. Budgets are positive USD values with `week`, `month`, `quarter`, or `year` calendar periods. A provider budget replaces the agent budget for that provider. Pacing is unavailable if its metered scope has unknown cost.

## Diagnostics and remedies

| Warning | Cause | Remedy |
| --- | --- | --- |
| `stored ... source(s) are still on parser` | Stored rows use an older parser. | Run `eurysx collect`. |
| `stored source(s) no longer exist on disk` | Retained history is unreachable. | Inspect with `eurysx doctor`; do not delete to clear it. |
| `has last-good data` | Latest refresh failed. | Fix the source issue, then collect again. |
| `pricing configuration ignored` | Invalid JSONC/config shape. | Correct `config/pricing.jsonc`. |
| `preferences ... is invalid` | Invalid policy or budget. | Correct `config/preferences.jsonc`. |

Diagnostics go to stderr. JSON exports retain resolver and preference warnings in their provenance objects.

## Releasing to PyPI

The `v*` GitHub tag workflow validates tests, compilation, whitespace, package build, and metadata/tag version equality. It then publishes through the configured `pypi` environment using PyPI Trusted Publishing and creates a GitHub Release from the matching changelog entry.

Before tagging, set the same release version in `src/eurysx/__init__.py`, `pyproject.toml`, README, and `CHANGELOG.md`; run the development checks above; ensure the changelog has `## [X.Y.Z]`; commit and push; then create and push `vX.Y.Z`. PyPI publication is immutable: never reuse a version.

## Adding a collector

1. Add a fixed harness path and collector module that enumerates `Source` descriptors.
2. Normalize only metadata into `UsageEntry`; never retain conversation content.
3. Add its parser version to `collectors.PARSER_VERSIONS` and bump it for parser changes.
4. Register the fixed collector dispatcher and add sanitized fixture parity tests.
5. Keep pricing, storage, analysis, and presentation outside the collector.
