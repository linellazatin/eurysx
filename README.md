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

## Pricing

Copy `config/pricing.jsonc.sample` to your platform configuration directory as
`pricing.jsonc` to enable pricing sources or add explicit overrides. The sample
and changelog are included in the source distribution. Do not commit local
pricing state, generated reports, or the cache directory.

Pricing precedence is recorded cost, explicit override, enabled source priority,
then unknown. Reports show **known cost**, unknown-cost entries and tokens, and
priced-token coverage. A genuine recorded or configured `$0` cost is covered;
unknown pricing is never treated as free.

Installed Eurysx reads `pricing.jsonc` from a user-writable configuration path:

- macOS: `~/Library/Application Support/Eurysx/`
- Linux: `$XDG_CONFIG_HOME/Eurysx/` or `~/.config/Eurysx/`
- Windows: `%APPDATA%/Eurysx/`

Its cache lives in the matching platform cache path. Set `EURYSX_CONFIG_DIR` or
`EURYSX_CACHE_DIR` to override either directory.

## Current limits

- Claude Code's stats cache is aggregate-only. Eurysx excludes it from selected
  date ranges and reports a scope warning; it remains available for all-time use.
- The supported collectors are Claude Code, OpenCode, Pi, and Codex only.
- Pricing data is metadata only. Source pricing may be unavailable, in which case
  the relevant cost remains unknown.
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
