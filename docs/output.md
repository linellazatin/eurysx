# Report output reference

Applies to Eurysx v0.0.5. Two output forms: the terminal report and the JSON
`--output` file. Both derive from the same structured analysis result, so the
numbers they show match.

> Status: the JSON shape is not yet a frozen contract. It is documented here as
> the current state; Act III Phase 6 will version and stabilize it, and this
> file will be updated to the pinned shape at that point.

## Terminal report

Per agent, in order:

1. `TOTAL USAGE (ALL MODELS)` — requests, turns, tool calls, token totals,
   known cost.
2. `BREAKDOWN BY MODEL` — per model, same token and cost figures.
3. `COST PROJECTIONS PER TIME PERIOD` — daily, weekly, monthly, quarterly,
   yearly extrapolations from the selected window.
4. `TOKEN VOLUME PER TIME PERIOD` — token-scaled versions of the same periods.
5. `MODEL ACTIVITY VOLUME PER TIME PERIOD` — requests/turns/tool calls per
   period.
6. `DAILY ACTIVITY` — tokens and cost per day.
7. `SUMMARY STATISTICS` — sessions, usage entries, unique models.
8. `CACHE EFFECTIVENESS` — cache read ratio (reads / cache tokens) and cache
   efficiency ratio (reads / writes), when the inputs are non-zero.
9. `COST ANALYSIS` — known cost, unknown-cost entries and tokens, metered token
   coverage, route breakdown, top models by cost, and the model pricing source
   provenance trail.

With more than one agent reported, a `COMPARISON SUMMARY` table follows.

Filter selectors (`--model`, `--provider`, `--billing-mode`) narrow the rows
entering analysis; they do not add fields or change the shapes documented
below. An agent left with no matching rows prints `No usage data found` and,
for `--output`, still appears in `agent_stats` with zeroed totals.

Bounded period runs append a `PERIOD COMPARISON` section to each agent's
report: total tokens, known cost, usage entries, and model requests for the
current period versus the same-length previous window, with percent change.
All-time runs have no previous window and omit the section.

Privacy: prompts, responses, file contents, tool arguments, and tool results
are never persisted or printed. The report shows aggregate tokens, counts,
cost, and pricing provenance only.

## JSON `--output` contract

Top level:

| Field | Type | Meaning |
| --- | --- | --- |
| `analysis_period` | object | `start` (YYYY-MM-DD or `"ALL TIME"`), `end`, `label`. |
| `agents_analyzed` | list[str] | Agents present in the report, in analyzed order. |
| `pricing` | object | `config_file`, `sources` (per-source fetch timestamps), `warnings`. |
| `preferences` | object | `config_file`, `warnings`. |
| `agent_stats` | object | Per-agent stats, keyed by agent name. |
| `period_comparison` | object | Per-agent previous-window comparison, keyed by agent name; empty `{}` for all-time runs where no previous window exists. See below. |

Per-agent stats block within `agent_stats`:

| Field | Type | Meaning |
| --- | --- | --- |
| `model_requests` / `model_turns` / `model_tool_calls` | int | Totals. |
| `total_input_tokens` / `total_output_tokens` | int | Tokens, excluding cache. |
| `total_cache_read_tokens` / `total_cache_write_tokens` | int | Cache tokens. |
| `total_tokens` | int | Grand total. |
| `total_cost` | float | Total cost (recorded or estimated). |
| `known_cost` | float | Cost with a known price. |
| `unknown_cost_count` / `unknown_cost_tokens` | int | Unpriced usage. |
| `priced_token_coverage` | float \| null | (metered - unknown) / metered; null when no metered tokens. |
| `cache_read_ratio` / `cache_efficiency_ratio` | float \| null | Cache section ratios, null when not computable. |
| `metered_tokens` | int | Tokens that resolve per-token pricing. |
| `non_metered_tokens` / `billing_mode_tokens` | object | `{billing_mode: tokens}`, modes such as `metered`, `subscription`, `credit`, `quota`, `local`. |
| `route_breakdown` | object | `"{provider}/{model} [{billing_mode}]"` → `{cost, entries, model_requests, model_tool_calls, model_turns, tokens}`. |
| `cost_status_counts` | object | `{status: count}`; statuses: `recorded`, `configured`, `cached`, `estimated`, `unknown`, `not_applicable`. |
| `pricing_sources` | list[str] | Sorted provenance, e.g. `recorded`, `override`, a source name. |
| `pricing_fetched_at` | object | `{source: ISO timestamp}`. |
| `daily_cost` / `weekly_cost` / `monthly_cost` / `quarterly_cost` / `yearly_cost` | float | Period extrapolations. |
| `usage_entries` / `sessions_count` | int | Entry and session counts. |
| `unique_models` | list[str] | Sorted model ids. |
| `model_breakdown` | object | `{model_id: {input, output, cache_read, cache_write, cost, model_requests, model_turns, model_tool_calls}}`. |
| `project_breakdown` | object | `{project_id: ...}` same shape as `model_breakdown`; unattributed rows fall under the `unknown` key. |
| `session_breakdown` | object | `{session_id: ...}` same shape as `model_breakdown`; unattributed rows fall under the `unknown` key. |
| `daily_activity` | object | `{"YYYY-MM-DD": {cost, tokens}}`. |
| `scope_warnings` | list[str] | Non-fatal note per agent (e.g. Claude Code aggregate exclusion). |

Period comparison block within `period_comparison` (present only when the run
has a bounded period):

| Field | Type | Meaning |
| --- | --- | --- |
| `current_period` / `previous_period` | string | Period labels (the previous window is the same length, ending the day before the current period starts). |
| `current` / `previous` | object | Compact figures: `total_tokens`, `known_cost`, `usage_entries`, `model_requests`. |

Notes:

- Recorded harness cost always wins; a conflict with policy is kept as recorded
  and warned about.
- `scope_warnings` entries are echoed as `Warning:` lines in the terminal
  report. Resolver and configuration warnings appear in the `pricing` and
  `preferences` blocks and on stderr.
- `N/A` in the terminal's metered-coverage line corresponds to a null
  `priced_token_coverage` in JSON.