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

- [ ] Push agent and date-range filtering into `store.events()` WHERE clauses,
  replacing the load-then-filter-in-Python path (claude-code aggregate
  handling must behave identically in selected ranges).
- [ ] Add indices on `provider`, `model_id`, `project_id`, and `session_id`.
- [ ] No new CLI flags; report output unchanged — proven by the Phase 1/2
  baseline tests plus a SQL-vs-Python equivalence test.

### Phase 3B: Filters and CLI selectors

- [ ] Add model, provider, and billing-mode filter parameters to `store.events()`.
- [ ] Add `--model`, `--provider`, and `--billing-mode` selectors, combinable
  with `--agent` and the period selectors.
- [ ] Tests per selector and for combinations, all routed through SQL.

### Phase 3C: Grouping dimensions

- [ ] Group the analysis result by model, provider, project, session, and day,
  where stored metadata supports them (`project_id`/`session_id` only where
  attributed).
- [ ] Show the new dimensions in terminal and JSON output as additive fields;
  tests per dimension including the unattributed-metadata case.

### Phase 3D: Period-to-period comparisons

- [ ] Compare two periods computed from the same store query path
  (e.g., previous period vs current).
- [ ] Terminal comparison table and additive JSON section, with tests.

### Phase 4: Richer metrics

- [ ] Add request/turn/tool ratios alongside the cache ratios Phase 1 already
  relocated into `AgentStats`.
- [ ] Report project and session usage and cost where `project_id` or
  `session_id` is attributed.
- [ ] Flag metrics that include last-good data from sources whose most recent
  refresh failed.

### Phase 5: Doctor diagnostics

- [ ] Add `eurysx doctor`: detected harnesses, per-source state (collected at,
  parser version, fingerprint changes, last error), pricing source and cache
  freshness, and configuration validation.
- [ ] Reuse the persisted diagnostics and existing resolver and preference
  warnings; introduce no new state.

### Phase 6: Stable exports

- [ ] Version the JSON report contract and document it as stable; the shape
  extended in Phase 1 is explicitly unstable history until this lands.
- [ ] Add CSV and Markdown presenters rendered from the same analysis result.
- [ ] Pin every export contract with tests, diffed against the Phase 1
  baseline snapshot.

### Phase 7: Pacing and insights

- [ ] Add deterministic budget pacing and insights only when their inputs are
  present and trustworthy.

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
