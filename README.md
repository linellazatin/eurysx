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
default, relative to the directory where you run `eurysx`:

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

Both files are JSONC: comments and trailing commas are allowed. Both are
optional. Without them, Eurysx retains recorded usage and marks unresolved cost
as unknown. Use `EURYSX_CONFIG_DIR` and `EURYSX_CACHE_DIR` only when deliberately
relocating those two directories, for example to a removable development volume.

### `preferences.jsonc`: agent and provider policy

`preferences.jsonc` declares a schema version 2 policy. An agent-level policy
applies to every model when the collector has no provider metadata. `providers`
overrides that policy only for an exact recorded provider. There are no
model-match rules.

```jsonc
{
  "schemaVersion": 2,
  "agents": {
    "claude-code": {
      "provider": "amazon-bedrock",
      "billingMode": "metered",
      "pricing": {
        "source": "amazon-bedrock",
        "otherSources": ["models-dev", "pi-models-store"]
      }
    },
    "codex": {
      "providers": {
        "openai": { "billingMode": "subscription" },
        "amazon-bedrock": {
          "billingMode": "metered",
          "pricing": { "source": "amazon-bedrock", "otherSources": ["models-dev"] }
        },
        "litellm": { "billingMode": "local" }
      }
    }
  }
}
```

`schemaVersion` and `agents` are the expected top-level fields. `schemaVersion`
is currently informational; Eurysx does not reject the file based on its value.
The file itself and each agent entry are optional. Omit an agent entry when you
want its usage to remain unclassified. Supported agent keys are `claude-code`,
`codex`, `opencode`, and `pi`; unknown keys are ignored.

| Policy field | Required? | Meaning |
| --- | --- | --- |
| `provider` | Optional | Effective provider when the collector does not record one. Usually needed for an agent-level metered policy. |
| `billingMode` | Optional | `metered`, `subscription`, `credit`, `quota`, `local`, or `unknown`. Omit it to let a matching provider policy decide; if no policy supplies it, Eurysx uses `unknown`. |
| `providers` | Optional | Map of exact recorded provider names to policy objects. Provider-policy fields override the agent-level fields. |
| `pricing` | Optional | Pricing lookup policy. For a config-priced metered route, include at least one valid `source` or `otherSources` entry. |
| `pricing.source` | Optional | Primary enabled source name from `pricing.jsonc`. |
| `pricing.otherSources` | Optional | Ordered enabled fallback source names from `pricing.jsonc`. |

`subscription`, `credit`, `quota`, and `local` report `N/A` incremental USD.
`metered` resolves pricing. A source name that is missing, disabled, or lacks an
exact provider/model price is skipped. Recorded harness cost always wins.

For an agent using more than one provider, omit agent-level `billingMode` rather
than setting it to `unknown` or an empty value. JSON has no useful blank value
here: `"billingMode": ""` is invalid policy data and is reported as unknown.
For example, Codex can classify its recorded OpenAI route as `subscription`, its
recorded Bedrock route as `metered`, and its recorded LiteLLM route as `local`.
If a record has no provider, or its provider is absent from `providers`, its
billing mode is `unknown` unless the agent-level policy defines one.

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
It has `sources`, provider-scoped `aliases`, and `overrides`. The file is
optional. Configure an enabled source before naming it in `pricing.source` or
`pricing.otherSources`.

```jsonc
{
  "schemaVersion": 2,
  "sources": {
    "amazon-bedrock": {
      "enabled": true,
      "profile": "your-aws-profile",
      "region": "ap-southeast-1",
      "refreshDays": 7
    }
  },
  "aliases": {
    "amazon-bedrock": {
      "claude-sonnet-4-6": "global.anthropic.claude-sonnet-4-6"
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

| Source | Required when enabled | Optional | Reads from |
| --- | --- | --- | --- |
| `amazon-bedrock` | `enabled: true`, `profile`, `region` | `refreshDays` | `aws pricing get-products` for Amazon Bedrock. |
| `pi-models-store` | `enabled: true` | `refreshDays` | `~/.pi/agent/models-store.json`. |
| `models-dev` | `enabled: true`, `url` | `refreshDays` | The configured models.dev-compatible URL. |

Aliases map collector model names to a source's canonical model ID without
changing billing policy. Override keys are `provider/model`; values are USD per
one million tokens. An override requires `input` and `output`; `cacheRead` and
`cacheWrite` are optional and default to zero. Never use a bare model override
to price multiple providers.

Resolution order is: recorded cost, explicit override, the route primary source,
the route's `otherSources` in order, then unknown. Eurysx never guesses a number
for an unpriced model.

### Refreshing and inspecting pricing

Eurysx creates `cache/` in the current project directory on every run. Enabled
sources cache normalized results in `cache/pricing-<source>.json`. Use the
normal command to use a fresh cache, or force a refresh:

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
OpenCode SQL fixture builds a temporary SQLite database during the test rather
than committing a binary database file.
