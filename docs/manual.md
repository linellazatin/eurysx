# Eurysx User manual

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
| `eurysx collect` | Collect and store metadata only, then refresh declared aggregate import files. |
| `eurysx report` | Read stored metadata without collection. |
| `eurysx doctor` | Read-only harness, source, aggregate import, cache, and configuration diagnostics. |

`doctor` does not parse sessions, fetch pricing, write cache files, or delete retained data.

`collect` also ingests the report files declared under `aggregate_imports` in
`preferences.jsonc`: it prints one status line per entry (`imported N aggregate row(s)`,
`unchanged aggregate import`, or `aggregate import failed`). A changed, unreadable file
records `last_error` and keeps the previously imported rows. `report` and `doctor` never
import: they read stored rows only.

## Selectors and exports

`--agent` accepts `claude-code`, `opencode`, `pi`, `codex`, or `all`. `all` cannot be combined with named agents. Period selectors are mutually exclusive: `--days`, `--weeks`, `--from` with optional `--to`, `--month`, `--quarter`, `--year`, and `--ytd`. `--model`, `--provider`, and `--billing-mode` combine with agent and period filters. `--billing-mode` accepts `metered`, `subscription`, `credit`, `quota`, `local`, or `unknown`.

The provider-reported aggregate lane is filtered by the period selector and `--model` only. `--provider` and `--billing-mode` classify local rows, so when either is active the lane renders a note that it is unfiltered by that selector instead of silently narrowing reported cost.

```bash
eurysx --agent codex --days 30
eurysx report --agent pi --provider unknown --month 2026-08
eurysx --agent all --output reports/usage.csv --format csv
eurysx --agent all --output reports/usage.md --format markdown
eurysx --agent all --days 30 --format html
eurysx --refresh-pricing
```

`--output PATH` writes a file. `--format json|csv|markdown|html` selects its format and defaults to JSON. CSV and Markdown require `--output`. HTML writes a new directory.

Terminal reports print one block per agent, then `PROVIDER-REPORTED AGGREGATES` when
aggregate rows are stored, then `COMPARISON SUMMARY` for multi-agent runs. The aggregate
block is printed outside every agent block so its figures cannot be read as agent usage.

JSON schema version 2 is stable. It contains `schema_version`, analysis period, pricing/preferences provenance, agent stats, `unresolved_routes`, `pacing`, period comparison, and the additive `aggregate_imports` lane. Estimate fields are additive: each agent has `actual_cost_usd`, `api_equivalent_estimate_usd`, `estimate_status_counts`, and metadata-only `estimate_entries`. An entry includes its estimate, effective provider/model, source provenance, rates, token dimensions, and calculation timestamp, never prompts, responses, tool data, proxy configuration, YAML text, or paths. Aggregate amounts are `0` when no matching amounts exist; absent per-entry rate dimensions are JSON `null`; unavailable estimates have a status count but no entry.

`aggregate_imports` always exists, even when nothing is configured, so consumers never branch on key presence:
| Field | Shape | Notes |
| --- | --- | --- |
| `provenance` | `"provider_reported_aggregate"` | Constant; the lane is never local, estimated, or recorded cost. |
| `totals` | object | `rows`, the four token dimensions, `rows_with_reported_cost`, and `reported_cost_usd`. |
| `by_date`, `by_model` | maps of the same bucket shape | Ungrouped cost rows roll up under `unknown`. |
| `sources` | map keyed by source file | `scope`, `kind`, `rows`, `source_mtime`, `ingested_at`, `newest_complete_date`. |
| `warnings` | array | Same strings as stderr diagnostics, prefixed `Warning:`. |
| `filters_not_applied` | array | Selectors that classify local rows only (`provider`, `billing-mode`). |
| `rows` | array | Stored aggregate rows, verbatim counters; `cost_usd` is decimal text or `null`. |

`totals.reported_cost_usd` is `null` when no row carries reported cost, never `0`: an
aggregate-only usage import has no cost to report. Token dimensions sum to `0` when the
source carries no token rows.

CSV, Markdown, and HTML render the same lane. CSV gains a trailing `reported_cost_usd`
column and marks its own rows with `agent` = `reported_aggregate`, `billing_mode` =
`aggregate`, and `cost_status` = `reported_aggregate`; local route rows leave
`reported_cost_usd` empty. Markdown prints a `## Provider-Reported Aggregates` section
after the agent sections. HTML puts one collapsible, sortable `Provider-Reported
Aggregates` disclosure on the combined index only, never on an agent page.

## Local state lifecycle

Default paths are relative to the current working directory:

```text
config/pricing.jsonc
config/preferences.jsonc
cache/pricing-<source>.json
data/eurysx.db
```

`EURYSX_CONFIG_DIR`, `EURYSX_CACHE_DIR`, and `EURYSX_DATA_DIR` relocate them. Cache files are disposable pricing metadata. The SQLite store retains last-good events when source parsing fails or source files disappear; `report` never deletes or re-normalizes rows.

The store is at schema version 2. Tables: `sources` (fingerprint, parser version, collected
at, last error for every harness and import source), `events` (normalized harness rows),
and `aggregate_imports` (provider-reported aggregate rows, keyed to their import source and
never joined to `events`). A schema version 1 store is upgraded in place on first open by
creating `aggregate_imports`; no rows move and no history is dropped. A store from a newer
build raises `unsupported Eurysx store schema version`. Deleting `data/eurysx.db` costs the
incremental index only: harness history is re-collected from local files, and imported
aggregates are re-collected from the report files the user saved. Parser changes bump the
matching collector version (Claude Code 1, Codex 2, Pi 3, OpenCode 3) or
`imports.anthropic_usage.PARSER_VERSION` (1), which forces the affected sources to
re-normalize on the next `collect`.

## Configuration

Copy the tracked JSONC samples. JSONC permits comments and trailing commas. Both files are optional: without them, recorded cost remains available and other cost may be unknown.

```bash
cp config/pricing.jsonc.sample config/pricing.jsonc
cp config/preferences.jsonc.sample config/preferences.jsonc
```

### `preferences.jsonc`: agent and provider policy

`schemaVersion` is informational; the current sample value is `3`. `agents` is an optional object whose supported keys are `claude-code`, `codex`, `opencode`, and `pi`. Unknown agent keys are ignored. Each agent policy may contain `provider`, `billingMode`, `pricing`, `budget`, and `providers`.

`providers` maps an exact observed provider string to a policy. Its policy overrides the agent policy. A provider policy may contain `modelIdRules`, a list of rules with exactly one string matcher: `exact` or `prefix`. Exact wins; then the longest matching prefix wins. A matching rule may override `provider`, `billingMode`, `pricing`, and `budget`.

| Field | Valid fixed values | Notes |
| --- | --- | --- |
| `billingMode` | `metered`, `subscription`, `credit`, `quota`, `local`, `unknown` | Omitted means `unknown` unless an inherited policy supplies a value. |
| `pricing.estimateApiEquivalent` | `true`, `false` | Optional; defaults to `false`. `true` requires an exact configured price and calculates an API-equivalent estimate without changing billing mode, known cost, coverage, pacing, or comparisons. |
| `budget.period` | `week`, `month`, `quarter`, `year` | `budget.usd` must be positive. |
| `modelIdRules` matcher | exactly one of `exact`, `prefix` | Both values are literal strings; no regular expressions or fuzzy matching. |

All other policy values are user-selected strings: `provider` is the effective pricing provider, provider-map keys are observed provider names, and `pricing` source names must exactly name enabled entries in `pricing.jsonc`. A non-boolean `estimateApiEquivalent` warns and behaves as `false`.

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

`subscription`, `credit`, `quota`, and `local` remain `N/A` incremental USD. An enabled API-equivalent estimate is never an invoice, budget spend, or billing classification. Harness-recorded cost remains authoritative and may coexist with an estimate. Terminal, CSV, Markdown, and HTML reports distinguish actual-recorded cost from API-equivalent estimates in totals and breakdowns; JSON exports transparent per-estimate metadata.

### `aggregate_imports`: provider-reported aggregate reports

`aggregate_imports` is an optional array. Each entry is an object with exactly three
required fields and no others:

| Field | Valid values | Notes |
| --- | --- | --- |
| `type` | `anthropic-usage-report`, `anthropic-cost-report` | Must name a supported reader; an unknown type warns and the entry is ignored. |
| `path` | explicit file or glob path | No default. One entry is one source: every file the glob matches is imported together and replaced atomically, so all paginated pages must share the declared path. `~` is expanded. |
| `scope` | non-empty free-text string | The account or organization label the user writes; it is display metadata, never an inferred identifier. |

```jsonc
{
  "schemaVersion": 3,
  "agents": { /* unchanged */ },
  "aggregate_imports": [
    {
      "type": "anthropic-usage-report",
      "path": "~/reports/anthropic/usage-*.json",
      "scope": "acme-organization"
    },
    {
      "type": "anthropic-cost-report",
      "path": "~/reports/anthropic/cost-*.json",
      "scope": "acme-organization"
    }
  ]
}
```

These entries point at report files you saved yourself: Eurysx makes no API call and never
reads a credential. An import is aggregate cost data, not a price source and not billing
policy, so it never enters price resolution, `pricing.jsonc` precedence, or the pricing
cache. A malformed entry, an unknown type, or a missing `path`/`scope` becomes a
`Warning:` and the entry is ignored; the shipped sample keeps every entry commented out so
a fresh checkout imports nothing.

### `pricing.jsonc`: price sources and manual overrides

`schemaVersion` is informational; the current sample value is `2`. `sources`, `aliases`, and `overrides` are optional objects. Prices are USD per one million tokens. `sources.<name>.enabled` is `true` to load a source or `false` to ignore it. `refreshDays` and `priority` are optional integers; invalid values warn and use `7` and `100` respectively. Lower `priority` values resolve first.

Supported source names and source-specific fields are:

| Source | Required when `enabled: true` | Optional | Reads from |
| --- | --- | --- | --- |
| `amazon-bedrock` | `profile`, `region` | `refreshDays`, `priority` | `aws pricing get-products` for Amazon Bedrock. |
| `pi-models-store` | none | `refreshDays`, `priority` | `~/.pi/agent/models-store.json`. |
| `models-dev` | `url` | `refreshDays`, `priority` | The configured models.dev-compatible URL. |
| `litellm-proxy` | `path`, `provider` | `refreshDays`, `priority` | A user-created sanitized metadata YAML file, never a LiteLLM proxy config. |

`litellm-proxy.path` must contain only `model_list` entries with `model_name` and `model_info` cost fields (`input_cost_per_token`, `output_cost_per_token`, and optional cache creation/read costs). Its `provider` is the exact effective provider mapped from the observed proxy name in `preferences.jsonc`. Eurysx rejects proxy-config keys and never caches the YAML text or path.

`aliases` maps an exact provider/model key to that source's canonical model ID. `overrides` keys are exact `provider/model` routes. Every override needs `input` and `output`; optional `cacheRead` and `cacheWrite` default to zero. Do not use a bare model key to price multiple providers.

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

Resolution order is recorded harness cost, explicit override, the route primary source, the route's `otherSources` in order, then unknown. Exact provider/model matching is required; Eurysx never guesses prices. Current pricing-source kinds are `recorded`, `override`, `official`, `catalog`, `not_applicable`, and `unknown`.

### Refreshing and inspecting pricing

Enabled sources cache normalized results in `cache/pricing-<source>.json`. Force a refresh with:

```bash
eurysx --refresh-pricing
eurysx doctor
```

If refresh fails, including a LiteLLM metadata read or parse failure, Eurysx uses a valid existing cache and emits a warning. The cache contains normalized pricing metadata, not prompts, credentials, LiteLLM YAML text, or metadata paths. Do not put API keys or LiteLLM master keys in either configuration file.

## Collectors

| Harness | Source granularity | Attribution / limit |
| --- | --- | --- |
| Claude Code | stats cache plus transcript metadata | Aggregate stats are excluded from bounded ranges. |
| OpenCode | one database | Project when session directory exists. |
| Pi | one session file | Session header and working directory when present. |
| Codex | one session file | Session and working directory when present. |
| Anthropic Usage & Cost (aggregate import, not a harness) | user-supplied saved report files, one source per declared glob | Organization/account scope, no session or project attribution. |

Collectors normalize usage metadata only. Parser read failures propagate to collection, where last-good store data is retained.

## Provider-reported aggregate imports

Anthropic publishes organization-level daily usage and cost reports. Eurysx reads only
files the user saved themselves: there is no API client, no credential read, and no
network call of any kind.

An import is an aggregate, not session data. Imported rows are a distinct row type, live
in their own store table, and are never `UsageEntry`, so they cannot reach pricing,
attribution, project or session breakdowns, known cost, actual cost, API-equivalent
estimates, metered coverage, cost-status counts, pacing, or period comparisons. Imported
cost carries the provenance label `provider_reported_aggregate`.

### Normalized row contract

| Field | Meaning |
| --- | --- |
| `source_kind` | `anthropic-usage-report` or `anthropic-cost-report` |
| `date`, `bucket_end` | UTC day bucket boundaries |
| `model` | reported model, `unknown` when the report is ungrouped |
| `input_uncached_tokens`, `input_cached_tokens`, `cache_creation_tokens`, `output_tokens` | token dimensions; usage reports only |
| `cost_usd`, `cost_type` | reported USD converted from cents; cost reports only |
| `workspace_id`, `service_tier`, `context_window`, `inference_geo`, `speed` | reported scope dimensions; `null` is a real value (default workspace, unspecified tier) |
| `scope_label` | the account label the user configured |
| `source_file`, `source_mtime` | provenance; a saved file carries no fetch timestamp, so file mtime is the freshness proxy |
| `ingested_at`, `parser_version` | collection time and reader version |
| `complete` | `false` while the bucket's UTC day is still in progress |

A usage row never carries cost and a cost row never carries tokens: the two lanes are not
combined into one figure. Reported USD totals sum only rows with a non-null `cost_usd`, and
a usage-only import renders its cost total as `N/A`, never `$0.00`.

### Freshness, limits, and required user action

The user must save the report files themselves and declare their paths (see
`aggregate_imports` under Configuration). Anthropic data appears within roughly five
minutes of request completion, so the newest day is partial, and one response covers at
most 31 daily buckets: save every page into the same declared glob. Duplicate buckets that
disagree keep the first file's value and emit a counted warning.

Organization aggregates exclude Amazon Bedrock, Google Vertex, Microsoft Foundry, and other
cloud-provider routes, which keep their own billing sources. Claude Code usage on a Pro or
Max subscription is included usage, not an invoice, and an imported aggregate is never
allocated across local sessions.

Report field names are pinned in `src/eurysx/imports/anthropic_usage.py` (`REPORT_KEYS`,
`COST_KEYS`) and are provisional until a live saved response is verified against
`tests/fixtures/anthropic_usage/`.

### Inspecting imports

`eurysx doctor` prints an `AGGREGATE IMPORTS` block after `PREFERENCES`, one line per
declared entry:

| Field | Meaning |
| --- | --- |
| `<n> file(s) matched` | Files the declared glob resolves to now; `0` means the reports are unreachable and stored rows are last-good. |
| `rows:` | Stored aggregate rows for those files. |
| `newest complete bucket:` | Newest imported day whose UTC day has closed, or `none`. |
| `collected <timestamp>` | When the source last ingested successfully, or `not collected`. |
| `last error:` | The failure that kept the previous rows, if any. |

A stored import source whose entry was removed from configuration is listed as `retained
import no longer configured` and its rows are kept. `doctor` is read-only: it never
re-parses report files, and it never deletes stored rows or import sources.

## Diagnostics and remedies

| Warning | Cause | Remedy |
| --- | --- | --- |
| `stored ... source(s) are still on parser` | Stored rows use an older parser. | Run `eurysx collect`. |
| `stored source(s) no longer exist on disk` | Retained history is unreachable. | Inspect with `eurysx doctor`; do not delete to clear it. |
| `aggregate import for <scope> has no matching files` | A declared import glob matched nothing. | Re-save the report files at that path, or remove the entry; stored rows are kept. |
| `aggregate import for <scope> ends on a truncated page (has_more=true)` | Only part of a paginated report was saved. | Save every page into the same declared glob. |
| `aggregate import for <scope> contains N duplicate bucket(s) with disagreeing values` | Two files report the same bucket with different numbers. | Keep one page per bucket; the first file's value wins. |
| `aggregate import for <scope> is stale` | The newest complete bucket is more than 3 days behind today. | Save a newer report at the declared path and run `collect`. |
| `newest imported bucket is still in progress` | The report includes the current UTC day, which Anthropic has not finished recording. | Read that day as partial; refresh the report after the day closes. |
| `imported aggregates overlap local claude-code aggregate usage` | The same window exists as an organization aggregate and as local stats-cache totals. | Treat them as two lanes; Eurysx never merges or subtracts them, because no stable join key exists. |
| `imported aggregate sources overlap each other` | Two declared entries cover intersecting days. | Narrow one entry's files or scopes; shared days may double-count. |
| `has last-good data` | Latest refresh failed. | Fix the source issue, then collect again. |
| `pricing configuration ignored` | Invalid JSONC/config shape. | Correct `config/pricing.jsonc`. |
| `preferences ... is invalid` | Invalid policy or budget. | Correct `config/preferences.jsonc`. |

Diagnostics go to stderr. JSON exports retain resolver and preference warnings in their provenance objects.

## Adding a collector

1. Add a fixed harness path and collector module that enumerates `Source` descriptors.
2. Normalize only metadata into `UsageEntry`; never retain conversation content.
3. Add its parser version to `collectors.PARSER_VERSIONS` and bump it for parser changes.
4. Register the fixed collector dispatcher and add sanitized fixture parity tests.
5. Keep pricing, storage, analysis, and presentation outside the collector.
