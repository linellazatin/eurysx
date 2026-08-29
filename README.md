# Eurysx

Local-first usage observability for Claude Code, OpenCode, Pi, and Codex.

Eurysx reads local agent history and reports tokens, requests, turns, tool calls,
estimated or recorded cost, and pricing provenance. It does not upload usage data
or persist prompts, responses, file contents, tool arguments, or tool results.

Eurysx is an Act I work in progress. It is a local CLI, not a hosted service.

## Install

```bash
python3 -m pip install .
eurysx --help
```

## Run

```bash
eurysx --version
eurysx
eurysx --agent codex --days 30
eurysx --agent all --month 2026-08
eurysx --agent pi --from 2026-08-01 --to 2026-08-15
eurysx --agent all --days 30 --output reports/usage.json
eurysx --refresh-pricing
```

`reports/` is ignored by Git.

Period selectors are mutually exclusive: `--days N`, `--weeks N`,
`--from YYYY-MM-DD [--to YYYY-MM-DD]`, `--month YYYY-MM`,
`--quarter YYYY-QN`, `--year YYYY`, and `--ytd`. Rolling periods include today.

## Configuration manual

Eurysx is checkout-local during Act I. It reads and writes only these paths by
default:

```text
eurysx/
├── config/pricing.jsonc
├── config/preferences.jsonc
└── cache/
```

It does not use `~/Library/Application Support`, XDG directories, or other
system-level application folders. The local configuration and cache are ignored
by Git. Create them from the tracked templates:

```bash
cp config/pricing.jsonc.sample config/pricing.jsonc
cp config/preferences.jsonc.sample config/preferences.jsonc
```

Both files are JSONC: comments and trailing commas are allowed. Missing files
are valid: Eurysx retains recorded usage and marks unresolved cost as unknown.
Use `EURYSX_CONFIG_DIR` and `EURYSX_CACHE_DIR` only when deliberately relocating
those two directories, for example to a removable development volume.

### `preferences.jsonc`: route and billing policy

`preferences.jsonc` declares how each supported agent's observed provider and
model should be interpreted. It contains all four supported agent keys:
`claude-code`, `codex`, `opencode`, and `pi`.

```jsonc
{
  "schemaVersion": 1,
  "agents": {
    "codex": {
      "default": { "billingMode": "unknown" },
      "routes": [
        {
          "match": { "provider": "openai" },
          "set": { "billingMode": "subscription" }
        },
        {
          "match": { "provider": "amazon-bedrock" },
          "set": {
            "billingMode": "metered",
            "pricingProvider": "amazon-bedrock"
          }
        },
        {
          "match": { "provider": "litellm" },
          "set": { "billingMode": "local" }
        }
      ]
    }
  }
}
```

Every agent needs a `default` object and a `routes` list. `default` applies when
no route matches. A route has a `match` object and a `set` object:

| Field | Location | Meaning |
| --- | --- | --- |
| `provider` | `match` | Exact recorded provider, such as `openai`, `amazon-bedrock`, or `litellm`. |
| `model` | `match`, optional | Exact recorded model ID. Omit it to match every model for that provider. |
| `billingMode` | `default` or `set` | One of `metered`, `subscription`, `credit`, `quota`, `local`, or `unknown`. |
| `provider` | `set`, optional | Replaces the effective route provider while retaining the observed provider in reports. |
| `pricingProvider` | `set`, optional | Provider identity used for pricing lookup. |
| `pricingModel` | `set`, optional | Model ID used for pricing lookup. |

A provider-and-model route is more specific than a provider-only route. If two
equally specific routes match, Eurysx uses the first and reports a diagnostic.
Rules never infer a route from a model name. Inspect the JSON report's
`observed_provider` and `model_id` before adding a rule.

For your current setup, use provider-wide LiteLLM matching because the proxy can
route many model IDs:

```jsonc
{
  "match": { "provider": "litellm" },
  "set": { "billingMode": "local" }
}
```

Add a model constraint only for an exception, such as one proxy model that
forwards to a metered Bedrock route:

```jsonc
{
  "match": { "provider": "litellm", "model": "bedrock.gpt-5.6" },
  "set": {
    "billingMode": "metered",
    "pricingProvider": "amazon-bedrock",
    "pricingModel": "bedrock.gpt-5.6"
  }
}
```

Billing modes describe incremental cost, not capability:

| Mode | Eurysx cost treatment |
| --- | --- |
| `metered` | Resolves per-token USD pricing. |
| `subscription`, `credit`, `quota`, `local` | Reports `N/A` incremental USD. Eurysx does not allocate fees or convert credits. |
| `unknown` | Retains usage and reports cost as unknown until you classify it or add pricing. |

Recorded harness cost always wins. If a recorded cost conflicts with a
non-metered policy, Eurysx keeps the recorded cost and warns.

### `pricing.jsonc`: price sources and manual overrides

`pricing.jsonc` supplies per-million-token USD prices only for `metered` usage.
It has two top-level objects: `sources` and `overrides`.

```jsonc
{
  "sources": {
    "aws-bedrock": {
      "enabled": true,
      "priority": 1,
      "profile": "your-aws-profile",
      "region": "ap-southeast-1",
      "refreshDays": 7
    }
  },
  "overrides": {
    "amazon-bedrock/bedrock.gpt-5.6": {
      "input": 0,
      "output": 0,
      "cacheRead": 0,
      "cacheWrite": 0
    }
  }
}
```

Supported sources are:

| Source | Required settings | Reads from |
| --- | --- | --- |
| `aws-bedrock` | `enabled`, `profile`, `region`; optional `priority`, `refreshDays` | `aws pricing get-products` for Amazon Bedrock. |
| `pi-models-store` | `enabled`; optional `priority`, `refreshDays` | `~/.pi/agent/models-store.json`. |
| `models-dev` | `enabled`, `url`; optional `priority`, `refreshDays` | The configured models.dev-compatible URL. |

Lower `priority` wins when two enabled sources price the same exact
provider/model. Override keys are `provider/model`; use the same provider and
model IDs as the effective `pricingProvider` and `pricingModel`. Values are USD
per one million tokens. `cacheRead` and `cacheWrite` are optional and default to
zero. Never use a bare model override to price multiple providers.

Resolution order is: recorded cost, explicit override, enabled source by
priority, then unknown. Eurysx never guesses a number for an unpriced model.

### Refreshing and inspecting pricing

Enabled sources cache their normalized results in `cache/pricing-<source>.json`.
Use the normal command to use a fresh cache, or force a refresh:

```bash
eurysx --refresh-pricing
```

If refresh fails, Eurysx uses a valid existing cache and reports a warning. If
no valid price is available, the associated metered usage remains unknown rather
than being reported as free. The cache contains pricing metadata, not prompts or
credentials. Do not add API keys or LiteLLM master keys to either configuration
file.

## Current limits

- Claude Code's stats cache is aggregate-only. Eurysx excludes it from selected
  date ranges and reports a scope warning; it remains available for all-time use.
- The supported collectors are Claude Code, OpenCode, Pi, and Codex only.
- Pricing data is metadata only. Source pricing may be unavailable, in which case
  the relevant cost remains unknown.
- Preferences use recorded route metadata or exact user rules. Eurysx does not
  call LiteLLM or provider APIs for route discovery and never reads credentials.
- A report with usage entries but no tokens displays priced-token coverage as
  `N/A`.
- Collector fixtures verify known file shapes, not every live harness release.

## Development

```bash
python3 -m unittest -v test_eurysx.py
python3 -m py_compile eurysx.py
```

Sanitized collector fixtures cover Claude Code, Codex, Pi, and OpenCode. The
OpenCode test builds a temporary SQLite database rather than committing one.
