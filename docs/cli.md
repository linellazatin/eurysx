# CLI reference

Applies to Eurysx v0.0.6. The terminal command is `eurysx`.

## Command forms

| Form | Behavior |
| --- | --- |
| `eurysx` (no command) | Collects current local metadata for detected agents, then reports it. |
| `eurysx collect` | Stores metadata only; prints no report. |
| `eurysx report` | Reads the local store without collecting. Requires the store to already exist. |

`report` never re-normalizes stored rows: after a collector's parser version
bumps, stored events are only rewritten by the next `eurysx` or `eurysx collect`
run. Between the upgrade and that run, a `report` can read narrower than the
store holds (e.g. OpenCode rows stored before parser v3 keep epoch-millis
timestamps, which the period filter drops, so a bounded `report` shows
`No usage data found` while the all-time view still totals them).

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
for the all-time view, where its `lastComputedDate` stamp (a plain `YYYY-MM-DD`)
is read as the first-usage date. Because that one row summarizes all recorded
history, all-time Claude Code extrapolations attribute the whole aggregate over
that span and its `DAILY ACTIVITY` section stays empty.

Bounded runs (any period selector set) also compare the period against the
same-length window that ends the day before it starts, in a per-agent
`PERIOD COMPARISON` section; all-time runs have no previous window and omit it.

## Filter selectors

Filters narrow the analyzed rows and combine freely with `--agent` and the
period selectors; multiple values within one flag are OR-ed, flags across are
AND-ed. Without filters, all rows match.

| Flag | Meaning |
| --- | --- |
| `--model ID...` | Only include usage for these model IDs (SQL filtered). |
| `--provider NAME...` | Only include usage for these providers (SQL filtered). Rows without a recorded provider match `unknown` (e.g. `--provider unknown`). |
| `--billing-mode MODE...` | Only include usage with these billing modes: `metered`, `subscription`, `credit`, `quota`, `local`, `unknown`. Applied after pricing: a recorded cost overrides policy, so a policy-subscription row with recorded cost is `metered`. |

Agents whose stored rows all fall outside the filters keep their report block
and print `No usage data found` rather than disappearing.

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
- stderr carries diagnostics that never enter the report: resolver and
  preference warnings, per-source refresh failures (last-good data retained),
  sources still stored on an older parser version, and sources whose files no
  longer exist on disk. The last two are advisory only: nothing is deleted and
  no analyzed number changes. `eurysx doctor` (planned, Act III Phase 5) will
  expose them as a per-source view.

## Examples

```bash
eurysx                          # collect detected agents, report all time
eurysx --agent codex --days 30  # last 30 days, Codex only
eurysx --agent all --month 2026-08
eurysx --agent pi --from 2026-08-01 --to 2026-08-15
eurysx --agent all --days 30 --output reports/usage.json
eurysx --agent codex --model gpt-5.6 --days 30
eurysx --provider anthropic --month 2026-08
eurysx --agent pi --billing-mode metered --from 2026-08-01 --to 2026-08-15
eurysx report --agent opencode --provider unknown --days 30
eurysx --refresh-pricing
eurysx collect --agent codex    # metadata only
eurysx report --agent codex --days 30   # no collection
```