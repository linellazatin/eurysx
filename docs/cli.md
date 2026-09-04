# CLI reference

Applies to Eurysx v0.0.4. The terminal command is `eurysx`.

## Command forms

| Form | Behavior |
| --- | --- |
| `eurysx` (no command) | Collects current local metadata for detected agents, then reports it. |
| `eurysx collect` | Stores metadata only; prints no report. |
| `eurysx report` | Reads the local store without collecting. Requires the store to already exist. |

`-v` / `--version` prints the installed version and exits.

## Agents

`--agent` selects which of the four harnesses to analyze. Choices: `claude-code`,
`opencode`, `pi`, `codex`, `all`. Default is `all`.

- `all` auto-detects installed harnesses (it cannot be combined with other agent
  names).
- Without `--agent`, behavior is the same as `--agent all`.
- In `report` mode, `all` reports every agent with stored data.

## Period selectors

Exactly one selector applies per run; the group is mutually exclusive.

| Flag | Meaning |
| --- | --- |
| `-d N` / `--days N` | Last N days, including today. |
| `-w N` / `--weeks N` | Last N x 7 days, including today. |
| `--from YYYY-MM-DD [--to YYYY-MM-DD]` | DateTime range. `--to` requires `--from`; `--from` must be on or before `--to`. |
| `--month YYYY-MM` | Calendar month. |
| `--quarter YYYY-QN` | Calendar quarter (N = 1..4). |
| `--year YYYY` | Calendar year. |
| `--ytd` | Year to date. |

No selector means all time. Claude Code's aggregate stats cache is excluded
from selected (non-all-time) ranges with a scope warning; it remains available
for the all-time view.

## Output and pricing control

| Flag | Meaning |
| --- | --- |
| `--output PATH` | Also writes the JSON report to PATH (see [output.md](output.md)); disables ANSI colors in the terminal report. |
| `--refresh-pricing` | Forces a refresh of enabled remote pricing sources instead of using `cache/`. If a refresh fails, a valid existing cache is used with a warning. |

## Diagnostics and exit behavior

- Unsupported agent names and invalid dates are rejected by the argument parser.
- If `report` is used before any collection, or no stored data matches the
  selection, Eurysx prints `No stored usage data found. Run eurysx collect
  first.` and exits.
- `all` with no detected harnesses prints `No agents detected. Check if any
  agents are installed.`
- Declared selectors are validated before any collection runs.

## Examples

```bash
eurysx                          # collect detected agents, report all time
eurysx --agent codex --days 30  # last 30 days, Codex only
eurysx --agent all --month 2026-08
eurysx --agent pi --from 2026-08-01 --to 2026-08-15
eurysx --agent all --days 30 --output reports/usage.json
eurysx --refresh-pricing
eurysx collect --agent codex    # metadata only
eurysx report --agent codex --days 30   # no collection
```