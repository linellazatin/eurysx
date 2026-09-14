# Eurysx Roadmap

> Acts describe durable product outcomes. Versions mark shipped states inside an
> Act, including fixes and small enhancements. Git commits, tags, and releases
> are user-owned operations.

## Philosophy

Eurysx is local-first observability for AI coding agents.

- No account, telemetry, cloud sync, proxy, daemon, or API key is required for
  local analysis.
- Analyze metadata, not conversation content: do not persist prompts, responses,
  file contents, tool arguments, or tool results by default.
- Costing is transparent and user-auditable. Preserve recorded cost, show the
  winning pricing source, and never present unknown pricing as a free model.
- Support a small number of verified harnesses well rather than chase broad,
  brittle compatibility.
- Prefer deterministic analysis and exports over AI-generated recommendations.

## Architectural Spine

```text
discover → collect → normalize → store → price → analyze → present
```

The pipeline gains seams only when variation earns them:

- Collectors locate one harness's data and normalize metadata. They do not price,
  store, analyze, or render.
- The event model carries normalized usage plus source provenance. It does not
  contain prompt or response content.
- Storage owns incremental ingestion and deduplication.
- Pricing resolves recorded costs, overrides, configured sources, and unknowns.
- Analysis consumes normalized events and resolved costs, not harness file formats.
- Presenters render analysis results; terminal, exports, HTML, and a later local
  dashboard share the same analysis results.

## Act I: First Sight

Establish Eurysx as a trustworthy, installable local CLI.

### Compatibility baseline

- [x] Create an independent Eurysx repository baseline.
- [x] Migrate the prototype into the `eurysx` module and CLI.
- [x] Preserve Claude Code, OpenCode, Pi, and Codex extraction behavior.
- [x] Preserve pricing precedence, cache fallback, and pricing provenance.
- [x] Ship a safe pricing configuration sample.
- [x] Port focused pricing regression tests.
- [x] Verify installed CLI help, syntax, and a Codex report smoke check.

### Trust and release

- [x] Add sanitized fixtures and collector-parity tests for Claude Code, Codex,
  OpenCode, and Pi: tokens, requests, turns, tool calls, model, provider,
  timestamp, and session identity where the harness records it. OpenCode uses a
  tracked SQL fixture to build its temporary SQLite database.
- [x] Make unknown cost visibly unknown in terminal and JSON output; distinguish
  known cost coverage from a genuinely zero-cost model.
- [x] Define rolling versus calendar date semantics and document the CLI contract.
- [x] Define checkout-local configuration and cache paths for Act I, with explicit
  environment overrides for a later portable relocation.
- [x] Add an explicit license file matching package metadata.
- [x] Ignore generated package artifacts and conventional local reports.
- [x] Include the pricing sample and changelog in the source distribution, then
  verify its installed CLI and documentation paths.
- [x] Render priced-token coverage as not applicable when no token entries exist.
- [x] Treat malformed pricing configuration values as diagnostics rather than
  startup exceptions.
- [x] Make OpenCode turn detection independent of SQLite message-row order.
- [x] Add a release check that the CLI and package metadata versions match.
- [x] Add user-owned route and billing preferences for each supported agent;
  preserve observed provider identity and never infer a route from model names.
- [x] Separate metered USD coverage from subscription, credit, quota, local,
  and unknown billing semantics in terminal and JSON reports.
- [x] Replace model-match preferences with readable agent defaults and provider
  overrides; add ordered configured-source fallbacks and checkout-local cache
  creation.
- [x] Document the JSONC configuration contract, including required settings for
  enabled price sources and optional agent- versus provider-level policies.
- [x] Review the Act I release checklist and record the v0.0.1 release. Git
  tags remain user-owned operations.

## Act II: Continuity

Make analysis incremental, reproducible, and privacy-preserving.

### Phase 1: Module seams

- [x] Migrate the released CLI to the `src/eurysx/` package without changing its
  reporting contract, then split its focused modules.
- [x] Keep CLI agent selection as a fixed mapping with one adapter per supported
  harness; collectors only discover and normalize local metadata.
- [x] Keep pricing, storage, analysis, and rendering downstream of collectors;
  use explicit collector roots in fixture tests.

### Phase 2: Durable metadata store

- [x] Add a project-local SQLite store for normalized metadata and decimal-text
  recorded costs.
- [x] Add stored-only reporting and a collect-only command.

### Phase 3: Incremental sources

- ~~[ ] Move collection to source-level adapters with fingerprints, cursors, and transactional source replacement.~~
- [x] Skip unchanged raw sources safely and retain usable history when one source
  fails to refresh.
- [x] Record source parser versions and refresh failures with each source.

### Phase 4: Attribution and diagnostics

- [x] Add best-effort project attribution and persisted collector diagnostics.

## Act III: Clarity

Make Eurysx useful for decisions and automation. Each phase lands, gets
verified, updates README, CHANGELOG, and the `docs/` reference files, and is re-assessed against the live
codebase before the next phase starts — no phase begins on assumption.
Version bumps land on request (git operations stay user-owned).

Documentation ownership: README stays the concise product and technical
overview. `docs/manual.md` (Phase 8) becomes the operational source of truth for
developers and agents; `docs/cli.md` and `docs/output.md` are today's reference
pages and are folded into it when that phase lands.

### Phase 1: Settle inherited gaps

- [x] Snapshot today's JSON `--output` shape and the terminal report's key
  sections in a regression test, as the pre-refactor baseline Phase 2 and
  Phase 6 diff against.
- [x] Move cache-read ratio and cache-efficiency ratio out of `render.py`'s
  terminal-only path into `analysis.py`/`AgentStats`, so JSON export reports
  the same numbers the terminal already shows.

### Phase 2: Result seams

- [x] Extend the Phase 1 baseline test with value assertions (not just key
  shape) over a controlled fixture, using hermetic pricing/preferences config,
  so the refactor cannot silently change a field's value or type.
- [x] Formalize one structured analysis result (period, per-agent stats, cost
  coverage, pricing provenance, warnings) that analysis produces before any
  presentation; terminal renderers consume it unchanged.
- [x] Move JSON payload assembly out of the CLI so the CLI only invokes
  collection, analysis, and a presenter.
- [x] Land this as a pure refactor against the Phase 1 baseline: output is
  unchanged except the Phase 1 cache-ratio fix. `store.events()` and its
  query path are out of scope here; they belong to Phase 3.

### Phase 3A: SQL pushdown and indices

- [x] Push agent and date-range filtering into `store.events()` WHERE clauses,
  replacing the load-then-filter-in-Python path (claude-code aggregate
  handling must behave identically in selected ranges).
- [x] Add indices on `provider`, `model_id`, `project_id`, and `session_id`.
- [x] No new CLI flags; report output unchanged — proven by the Phase 1/2
  baseline tests plus a SQL-vs-Python equivalence test.

### Phase 3B: Filters and CLI selectors

- [x] Add model and provider filter parameters to `store.events()` (SQL WHERE).
  `billing_mode` is deliberately NOT a stored column — it is a pricing-time
  artifact assigned by policy and flipped to `metered` when recorded cost
  conflicts with policy, so it filters post-pricing in
  `UsageAnalyzer.analyze_agent` instead: SQL would drop rows the report must
  keep with a conflict warning.
- [x] Add `--model`, `--provider`, and `--billing-mode` selectors, combinable
  with `--agent` and the period selectors.
- [x] Tests per selector and for combinations: model/provider routed through
  SQL (`--provider unknown` matches NULL providers via COALESCE),
  billing-mode through the post-pricing hook incl. the
  recorded-cost-flips-to-metered case.

### Phase 3C: Grouping dimensions

- [x] Group the analysis result by model, provider, project, session, and day,
  where stored metadata supports them (`project_id`/`session_id` only where
  attributed). Model/provider/day groups already existed in `AgentStats`;
  per-project and per-session groupings were added as
  `project_breakdown`/`session_breakdown`, same shape as `model_breakdown`,
  with an `unknown` bucket for unattributed rows.
- [x] Show the new dimensions in terminal and JSON output as additive fields;
  tests per dimension including the unattributed-metadata case (terminal
  prints `No ... attribution available.` when nothing is attributed).

### Phase 3D: Period-to-period comparisons

- [x] Compare two periods computed from the same store query path
  (previous same-length window vs current), reusing `store.events()` with the
  same filters and the post-pricing billing-mode hook for both periods.
- [x] Terminal `PERIOD COMPARISON` table (tokens, known cost, entries,
  requests with percent change) and additive `period_comparison` JSON section,
  with tests; all-time runs omit the comparison because no previous window
  exists.

### Phase 4: Richer metrics (closed — shipped in v0.0.6)

- [x] Add request/turn/tool ratios alongside the cache ratios Phase 1 already
  relocated into `AgentStats`: `requests_per_turn`, `tool_calls_per_request`,
  `tool_calls_per_turn`. Counts live on different row kinds per harness
  (Pi/Codex/OpenCode carry requests, turns, and tool calls on metric rows;
  Claude Code only on its aggregate), so totals already sum across both, but a
  ranged Claude Code report has no request/turn/tool rows at all: ratios are
  `null`/`N/A`, never zero or infinity.
- [x] ~~Report project and session usage and cost where `project_id` or
  `session_id` is attributed.~~ Delivered by Phase 3C as
  `project_breakdown`/`session_breakdown` (tokens, cost, and activity counts per
  bucket, terminal and JSON); nothing left to build here.
- ~~[ ] Flag metrics that include last-good data from sources whose most recent
  refresh failed. Deferred to Phase 5: `UsageEntry` carries no `source_key`,
  `store.failing_sources()` is not agent-scoped, and `source_key` embeds
  absolute paths, so the flag must be a count or boolean per agent. Phase 5
  already owns diagnostics and must not add state, so both land together.~~

#### v0.0.6 checkpoint fixes (pre-phase reassessment, shipped)

- [x] Parse date-only (`YYYY-MM-DD`) timestamps in
  `UsageAnalyzer.extract_date_from_timestamp`. Claude Code's stats cache stamps
  rows with `lastComputedDate` only, so all-time Claude Code reports showed an
  unpinned one-day period, `$0.00` cost rates next to a non-zero known cost,
  and an empty `DAILY ACTIVITY` table.
- [x] Keep aggregate rows out of `daily_activity` (they summarize all history
  on one stamp; a per-day bucket would be a false spike) and render the
  per-active-day projection lines as `n/a` when no per-day rows exist, instead
  of repeating the across-all-days figure under an active-days label.
- [x] Label the terminal session count as attributed when unattributed rows
  exist, so `Total sessions: 0` no longer contradicts the `unknown` bucket in
  `BREAKDOWN BY SESSION`.
- [x] Warn (stderr, read-only) about stored sources still on an older parser
  version than the current collector, and about stored sources whose files no
  longer exist on disk. `eurysx report` never re-normalizes stored rows, so a
  bounded report can otherwise read narrower than the store holds; deletion
  stays out of scope because an absent harness directory would destroy history.
- [x] Fix Pi session attribution (parser v3): live Pi files carry the session id
  only on the `type: "session"` header, so every stored Pi row had a null
  `session_id` — Phase 4's session metrics would have been empty for the harness
  with the most rows. Pi and Codex fixtures already carried `cwd` for project
  attribution; the Pi fixture now matches the live session-id shape too.

### Phase 5: Doctor diagnostics

- [x] Add `eurysx doctor`: detected harnesses, per-source state (collected at,
  parser version, fingerprint changes, last error), pricing source and cache
  freshness, and configuration validation.
- [x] Reuse persisted diagnostics and resolver/preference warnings without new
  state; reuse harness detection, parser versions, and retained-source checks.
- [x] Flag reports that may include last-good data after a failed refresh with
  agent-scoped source counts, never `source_key` paths. (Struck from Phase 4.)
- [x] Surface retained-but-unreachable history in doctor without deleting it.

### Phase 6: Pacing, insights, and pricing hardening

- [x] Improve pricing diagnostics for observed metered provider/model routes with
  exact provider-qualified lookups and ordered fallbacks. Never fuzzy-match,
  infer, or treat unresolved pricing as free; subscription/credit/quota/local
  usage remains `N/A` incremental USD.
- [x] Diagnose unresolved metered routes by exact provider/model and reason:
  missing metered policy, enabled source, or verified source mapping.
- [x] Add optional positive USD budgets to `preferences.jsonc` at agent level,
  with exact-provider overrides and validation warnings.
- [x] Add deterministic pacing only when a valid budget and complete known-cost
  inputs exist: spend, remaining budget, calendar pace, and on-track status.
  Otherwise report why pacing is unavailable.

### Phase 7: Stable exports

- [x] Version the JSON report contract and document it as stable.
- [x] Add CSV and Markdown presenters rendered from the same analysis result.
- [x] Pin every export contract with tests, diffed against the Phase 1
  baseline snapshot.

### Phase 8: Operational manual

Write the manual for developers and agents who operate Eurysx, not for readers
skimming a product page. Every command, flag, path, warning string, file, and
JSON field it names must be verified against a live run, and every failure mode
must have a named remedy.

- [x] Create `docs/manual.md` as the operational source of truth, with
  README demoted to the concise product and technical overview.
- [x] Manual contents: install and first run; full CLI contract
  including every selector, exit behavior, and stdout/stderr split; store and
  cache lifecycle on disk (what is disposable, what is retained, how a
  parser-version bump re-collects); per-harness collector surface (what each
  harness yields, source granularity, attribution keys, aggregate-only limits);
  output contracts field by field for terminal and JSON; pricing and preference
  precedence; a diagnostics table mapping each `Warning:` string to cause and
  remedy; and a worked guide to adding a fifth collector.
- [x] Fold `docs/cli.md` and `docs/output.md` into the manual rather than
  maintaining overlapping pages: keep one place per fact, with README, `AGENTS.md`,
  and CHANGELOG pointing at it.
- [x] Write it so an agent can execute against it: exact commands, absolute
  checkout-local paths, invariants stated as rules (recorded cost beats policy,
  unknown pricing is never free, no content fields, no deletions in `report`),
  and the release/version-bump checklist in one place.
- [x] Pin the manual against drift: a check that documented flags, warning
  strings, and JSON keys exist in the source (grep-level is enough; no new
  framework), so a phase that renames something cannot leave the manual behind.
- [x] Land the export-contract chapter after Phase 7 versions the JSON report;
  until then mark that chapter as tracking an unstable shape. Phase 8 itself is
  not blocked on Phases 5-7 and can start once Phase 4 closes.
- [x] Update the Act III docs convention in `AGENTS.md` to name `docs/manual.md`
  as the operational source of truth when this lands.

## Act IV: Local View

Provide useful local presentations without introducing a hosted product.

- [ ] Generate a self-contained HTML report from the same analysis results as the
  CLI and exports.
- [ ] Add overview, trends, model/agent breakdowns, pricing provenance, projects,
  and sessions where supported.
- [ ] Consider a read-only localhost dashboard only after HTML reports prove
  insufficient.
- [ ] Keep presentation logic separate from collection, pricing, storage, and
  analysis.

## Act V: Proof

Validate demand before expanding the business surface.

- [ ] Recruit and support independent users of the local CLI.
- [ ] Record repeated requests, friction, and actual workflow value.
- [ ] Test paid convenience or fixed-scope async workflow/cost audits.
- [ ] Consider team, multi-device, or hosted features only from demonstrated
  demand.

## Deferred Until Evidence

- Cloud sync, accounts, hosted dashboards, team organizations, SSO, RBAC, mobile
  or desktop apps, real-time watchers, background daemons, and editor extensions.
- LLM-based recommendations, API routing/proxying, prompt-quality scoring,
  conversation analysis, agent benchmarking, subscription allocation, and
  credit-to-USD conversion.
