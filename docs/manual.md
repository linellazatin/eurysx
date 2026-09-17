# Eurysx User manual

Eurysx is a local-first CLI for metadata-only usage analysis of Claude Code,
OpenCode, Pi, and Codex. It never persists prompts, responses, file content,
tool arguments, tool results, or credentials.

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

`doctor` does not parse sessions, fetch pricing, write cache files, or delete
retained data.

## Selectors and exports

`--agent` accepts `claude-code`, `opencode`, `pi`, `codex`, or `all`. `all`
cannot be combined with named agents. Period selectors are mutually exclusive:
`--days`, `--weeks`, `--from` with optional `--to`, `--month`, `--quarter`,
`--year`, and `--ytd`. `--model`, `--provider`, and `--billing-mode` combine
with agent and period filters. `--billing-mode` accepts `metered`,
`subscription`, `credit`, `quota`, `local`, or `unknown`.

```bash
eurysx --agent codex --days 30
eurysx report --agent pi --provider unknown --month 2026-08
eurysx --agent all --output reports/usage.csv --format csv
eurysx --agent all --output reports/usage.md --format markdown
eurysx --agent all --days 30 --format html
eurysx --refresh-pricing
```

`--output PATH` writes a file. `--format json|csv|markdown|html` selects its
format and defaults to JSON. CSV and Markdown require `--output`. HTML writes a
new directory.

JSON schema version 1 is stable. It contains `schema_version`, analysis period,
pricing/preferences provenance, agent stats, `unresolved_routes`, `pacing`, and
period comparison. Estimate fields are additive: each agent has
`actual_cost_usd`, `api_equivalent_estimate_usd`, `estimate_status_counts`, and
metadata-only `estimate_entries`. An entry includes its estimate, effective
provider/model, source provenance, rates, token dimensions, and calculation
timestamp, never prompts, responses, tool data, proxy configuration, YAML text,
or paths. Aggregate amounts are `0` when no matching amounts exist; absent
per-entry rate dimensions are JSON `null`; unavailable estimates have a status
count but no entry.

## Local state lifecycle

Default paths are relative to the current working directory:

```text
config/pricing.jsonc
config/preferences.jsonc
cache/pricing-<source>.json
data/eurysx.db
```

`EURYSX_CONFIG_DIR`, `EURYSX_CACHE_DIR`, and `EURYSX_DATA_DIR` relocate them.
Cache files are disposable pricing metadata. The SQLite store retains last-good
events when source parsing fails or source files disappear; `report` never
deletes or re-normalizes rows.

## Configuration

Copy the tracked JSONC samples. JSONC permits comments and trailing commas.
Both files are optional: without them, recorded cost remains available and other
cost may be unknown.

```bash
cp config/pricing.jsonc.sample config/pricing.jsonc
cp config/preferences.jsonc.sample config/preferences.jsonc
```

### `preferences.jsonc`: agent and provider policy

`schemaVersion` is informational; the current sample value is `3`. `agents` is
an optional object whose supported keys are `claude-code`, `codex`, `opencode`,
and `pi`. Unknown agent keys are ignored. Each agent policy may contain
`provider`, `billingMode`, `pricing`, `budget`, and `providers`.

`providers` maps an exact observed provider string to a policy. Its policy
overrides the agent policy. A provider policy may contain `modelIdRules`, a list
of rules with exactly one string matcher: `exact` or `prefix`. Exact wins; then
the longest matching prefix wins. A matching rule may override `provider`,
`billingMode`, `pricing`, and `budget`.

| Field | Valid fixed values | Notes |
| --- | --- | --- |
| `billingMode` | `metered`, `subscription`, `credit`, `quota`, `local`, `unknown` | Omitted means `unknown` unless an inherited policy supplies a value. |
| `pricing.estimateApiEquivalent` | `true`, `false` | Optional; defaults to `false`. `true` requires an exact configured price and calculates an API-equivalent estimate without changing billing mode, known cost, coverage, pacing, or comparisons. |
| `budget.period` | `week`, `month`, `quarter`, `year` | `budget.usd` must be positive. |
| `modelIdRules` matcher | exactly one of `exact`, `prefix` | Both values are literal strings; no regular expressions or fuzzy matching. |

All other policy values are user-selected strings: `provider` is the effective
pricing provider, provider-map keys are observed provider names, and `pricing`
source names must exactly name enabled entries in `pricing.jsonc`. A non-boolean
`estimateApiEquivalent` warns and behaves as `false`.

```jsonc
{
  "schemaVersion": 3,
  "agents": {
    "codex": {
      "providers": {
        "openai": { "billingMode": "subscription" },
        "my-homelab-proxy": {
          "provider": "homelab-litellm",
          "billingMode": "subscription",
          "pricing": {
            "source": "litellm-proxy",
            "estimateApiEquivalent": true
          },
          "modelIdRules": [
            { "prefix": "local.", "billingMode": "local" }
          ]
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

`subscription`, `credit`, `quota`, and `local` remain `N/A` incremental USD.
An enabled API-equivalent estimate is never an invoice, budget spend, or billing
classification. Harness-recorded cost remains authoritative and may coexist
with an estimate. Terminal, CSV, Markdown, and HTML reports distinguish
actual-recorded cost from API-equivalent estimates in totals and breakdowns;
JSON exports transparent per-estimate metadata.

### `pricing.jsonc`: price sources and manual overrides

`schemaVersion` is informational; the current sample value is `2`. `sources`,
`aliases`, and `overrides` are optional objects. Prices are USD per one million
tokens. `sources.<name>.enabled` is `true` to load a source or `false` to ignore
it. `refreshDays` and `priority` are optional integers; invalid values warn and
use `7` and `100` respectively. Lower `priority` values resolve first.

Supported source names and source-specific fields are:

| Source | Required when `enabled: true` | Optional | Reads from |
| --- | --- | --- | --- |
| `amazon-bedrock` | `profile`, `region` | `refreshDays`, `priority` | `aws pricing get-products` for Amazon Bedrock. |
| `pi-models-store` | none | `refreshDays`, `priority` | `~/.pi/agent/models-store.json`. |
| `models-dev` | `url` | `refreshDays`, `priority` | The configured models.dev-compatible URL. |
| `litellm-proxy` | `path`, `provider` | `refreshDays`, `priority` | A user-created sanitized metadata YAML file, never a LiteLLM proxy config. |

`litellm-proxy.path` must contain only `model_list` entries with `model_name` and
`model_info` cost fields (`input_cost_per_token`, `output_cost_per_token`, and
optional cache creation/read costs). Its `provider` is the exact effective
provider mapped from the observed proxy name in `preferences.jsonc`. Eurysx
rejects proxy-config keys and never caches the YAML text or path.

`aliases` maps an exact provider/model key to that source's canonical model ID.
`overrides` keys are exact `provider/model` routes. Every override needs `input`
and `output`; optional `cacheRead` and `cacheWrite` default to zero. Do not use
a bare model key to price multiple providers.

```jsonc
{
  "schemaVersion": 2,
  "sources": {
    "amazon-bedrock": {
      "enabled": true,
      "profile": "your-aws-profile",
      "region": "ap-southeast-1",
      "refreshDays": 7,
      "priority": 10
    },
    "pi-models-store": { "enabled": false },
    "models-dev": {
      "enabled": true,
      "url": "https://models.dev/api.json",
      "refreshDays": 7,
      "priority": 20
    },
    "litellm-proxy": {
      "enabled": true,
      "path": "/absolute/path/to/litellm-price-metadata.yaml",
      "provider": "homelab-litellm",
      "refreshDays": 7,
      "priority": 30
    }
  },
  "aliases": {
    "amazon-bedrock": {
      "claude-sonnet-4-6": "global.anthropic.claude-sonnet-4-6"
    }
  },
  "overrides": {
    "my-provider/my-model": {
      "input": 3,
      "output": 15,
      "cacheRead": 0.3,
      "cacheWrite": 3.75
    }
  }
}
```

Resolution order is recorded harness cost, explicit override, the route primary
source, the route's `otherSources` in order, then unknown. Exact
provider/model matching is required; Eurysx never guesses prices. Current
pricing-source kinds are `recorded`, `override`, `official`, `catalog`,
`not_applicable`, and `unknown`.

### Refreshing and inspecting pricing

Enabled sources cache normalized results in `cache/pricing-<source>.json`.
Force a refresh with:

```bash
eurysx --refresh-pricing
eurysx doctor
```

If refresh fails, including a LiteLLM metadata read or parse failure, Eurysx
uses a valid existing cache and emits a warning. The cache contains normalized
pricing metadata, not prompts, credentials, LiteLLM YAML text, or metadata
paths. Do not put API keys or LiteLLM master keys in either configuration file.

## Collectors

| Harness | Source granularity | Attribution / limit |
| --- | --- | --- |
| Claude Code | stats cache plus transcript metadata | Aggregate stats are excluded from bounded ranges. |
| OpenCode | one database | Project when session directory exists. |
| Pi | one session file | Session header and working directory when present. |
| Codex | one session file | Session and working directory when present. |

Collectors normalize usage metadata only. Parser read failures propagate to
collection, where last-good store data is retained.

## Diagnostics and remedies

| Warning | Cause | Remedy |
| --- | --- | --- |
| `stored ... source(s) are still on parser` | Stored rows use an older parser. | Run `eurysx collect`. |
| `stored source(s) no longer exist on disk` | Retained history is unreachable. | Inspect with `eurysx doctor`; do not delete to clear it. |
| `has last-good data` | Latest refresh failed. | Fix the source issue, then collect again. |
| `pricing configuration ignored` | Invalid JSONC/config shape. | Correct `config/pricing.jsonc`. |
| `preferences ... is invalid` | Invalid policy or budget. | Correct `config/preferences.jsonc`. |

Diagnostics go to stderr. JSON exports retain resolver and preference warnings
in their provenance objects.

## Releasing to PyPI

The `v*` GitHub tag workflow validates tests, compilation, whitespace, package
build, and metadata/tag version equality. Before tagging, set the same release
version in `src/eurysx/__init__.py`, `pyproject.toml`, README, and CHANGELOG.md;
run the development checks; ensure the changelog has `## [X.Y.Z]`; commit and
push; then create and push `vX.Y.Z`.

## Adding a collector

1. Add a fixed harness path and collector module that enumerates `Source` descriptors.
2. Normalize only metadata into `UsageEntry`; never retain conversation content.
3. Add its parser version to `collectors.PARSER_VERSIONS` and bump it for parser changes.
4. Register the fixed collector dispatcher and add sanitized fixture parity tests.
5. Keep pricing, storage, analysis, and presentation outside the collector.
