# eurysx

<div align="center">

[![eurysx version](https://img.shields.io/pypi/v/eurysx?logo=pypi&color=blue)](https://pypi.org/project/eurysx)

</div>

Local-first usage observability for Claude Code, OpenCode, Pi, and Codex.

Eurysx reads local agent history and reports tokens, requests, turns, tool calls,
estimated or recorded cost, and pricing provenance. It does not upload usage data
or persist prompts, responses, file contents, tool arguments, or tool results.

> Eurysx v0.1.3 is a local CLI, not a hosted service.

## What the name means

`Eurysx` is derived from *Euryphaessa*, an epithet associated with broad-shining
light. Its `eury` root suggests a wide view; `s` evokes stats or sight, and `x`
marks cross-agent work. It is a compact name for seeing usage across coding
harnesses without pretending that their routes, prices, or records are
interchangeable.

## Our philosophy

- Local history is evidence, not telemetry: usage stays on the machine and
  sensitive session content is neither persisted nor reported.
- Unknown stays unknown. Eurysx does not invent a price, provider route, or
  billing mode when the available records cannot support one.
- Recorded cost wins over policy. Configurations make user intent explicit;
  they do not rewrite what a harness recorded.
- One normalized view should preserve provenance. Comparable reports must still
  show where data and pricing came from.
- The tool remains a small, local CLI, not a hosted analytics service or an
  agent-management platform.

## Install

```bash
pip3 install eurysx
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
eurysx --agent all --days 30 --format html
eurysx --agent all --days 30 --output reports/august-usage --format html
eurysx --refresh-pricing
eurysx collect --agent codex
eurysx report --agent codex --days 30
eurysx doctor
```

`reports/` is ignored by Git. `eurysx` collects current local metadata and then
reports it. `collect` stores metadata only; `report` reads the local store
without collecting. `doctor` is terminal-only and reports local harness, source,
pricing-cache, and configuration health without parsing history, refreshing
prices, or deleting retained data.

Period selectors are mutually exclusive: `--days N`, `--weeks N`,
`--from YYYY-MM-DD [--to YYYY-MM-DD]`, `--month YYYY-MM`,
`--quarter YYYY-QN`, `--year YYYY`, and `--ytd`. Rolling periods include today.
Filter selectors `--model`, `--provider`, and `--billing-mode` combine with
agent and period selectors.

## Documentation

The [User manual](docs/manual.md) is the authoritative command, output,
configuration, pricing, and diagnostics reference.

## Current limits

- Claude Code's stats cache is aggregate-only; selected date ranges exclude it
  and report a scope warning.
- The supported collectors are Claude Code, OpenCode, Pi, and Codex only.
- Pricing data is metadata only. Source pricing may be unavailable, in which
  case the relevant cost remains unknown.
- Preferences use recorded route metadata or exact user rules. Eurysx does not
  call LiteLLM or provider APIs for route discovery and never reads credentials.
- API-equivalent estimates are opt-in, never invoices or budget spend. All
  report formats distinguish them from harness-recorded cost; see the User
  manual for the JSON fields and `N/A` behavior.

## Development

```bash
PYTHONPATH=src python3 -m unittest -v tests/test_eurysx.py
python3 -m py_compile src/eurysx/*.py
```

Sanitized collector fixtures cover Claude Code, Codex, Pi, and OpenCode. The
OpenCode SQL fixture builds a temporary SQLite database during the test rather
than committing a binary database file.
