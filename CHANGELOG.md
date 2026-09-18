# Changelog

## [0.1.5] - Anthropic aggregate-cost imports

### Added

- Sanitized Anthropic Usage & Cost report fixtures pinning the aggregate import contract (provisional, documentation-derived until a live response is verified).
- Manual chapter documenting the provider-reported aggregate contract, freshness limits, and exclusions.
- Normalized aggregate import row model and the Anthropic usage report reader; imported rows are never usage entries and are never priced.
- Anthropic cost report reader with exact cents-to-USD conversion, one import source per declared glob, duplicate-bucket conflict counting, and truncated-page disclosure.
- Store schema version 2 with the `aggregate_imports` table and an accepting in-place migration from version 1.
- `preferences.jsonc` `aggregate_imports` block with validation diagnostics; the shipped sample keeps every entry commented out.
- `eurysx collect` ingests declared aggregate imports with fingerprint skipping, last-good retention, and per-entry status lines.
- Provider-reported aggregate analysis lane with by-date and by-model rollups, per-source freshness, and scope, staleness, overlap, and double-count warnings.
- Terminal `PROVIDER-REPORTED AGGREGATES` block and JSON contract version 2 with the additive `aggregate_imports` lane and an explicit empty shape.
- CSV, Markdown, and HTML render the aggregate lane from the same analysis result; CSV gains a trailing `reported_cost_usd` column.
- `doctor` reports declared aggregate imports: matched files, stored rows, newest complete bucket, collected state, last error, and retained-but-unconfigured sources.

### Fixed

- `report` no longer hides the provider-reported lane when the local selection holds no harness rows: an imports-only store prints `No local harness usage stored; reporting provider-reported aggregates only.` and renders the lane in every format, and HTML export no longer assumes a token leader exists.
- `collect` ingests declared aggregate imports on a machine with no detected harness history instead of stopping at `No agents detected.`
- Invalid `aggregate_imports` entries are now reported. The block was validated after preference warnings had already been flushed, so `aggregate import entry ignored: ...` never reached stderr and a mistyped entry looked accepted.

### Tests

- Fixture-shape guards for both Anthropic report kinds, reader parity for cents-to-USD, dedupe, completeness, and truncated pages.
- Isolation proof that stored imports leave agent totals, known cost, coverage, pacing, comparisons, and the locked JSON baseline unchanged apart from the additive lane.
- Store migration, atomic replacement, incremental skip, last-good retention, configuration validation, doctor state, and all five presenters.
- Boundary and privacy guards: the reader package imports neither pricing, storage, nor presentation; imported rows are never usage entries; fixtures carry no identifiers or content fields.
- Imports-only paths: the lane renders with no local agent stats in terminal, JSON, CSV, Markdown, and HTML, and `collect` ingests declared imports when no harness is detected.
- `tests/live_test.py`: scripted live checks L1-L17 driving a real CLI process inside sandboxed `EURYSX_CONFIG_DIR`, `EURYSX_DATA_DIR`, `EURYSX_CACHE_DIR`, and `HOME`, with `--json`, `--only`, and `--old-tree` for agent invocation and cross-version comparison.

## [0.1.4] - Cost lanes and LiteLLM estimates

### Added

- Opt-in API-equivalent estimates with separate recorded-cost provenance.
- Sanitized LiteLLM YAML metadata pricing; proxy configs and credentials are never read.
- Additive JSON estimate fields and actual/estimate columns in all report formats.

### Changed

- Configuration reference consolidated in `docs/manual.md`.

### Tests

- Added coverage for estimate isolation, routing, validation, privacy, and stale-cache fallback.

## [0.1.3] - Pricing source reassessment

### Fixed

- Calculated metered prices now render as known cost rather than `N/A`.

### Added

- JSON pricing provenance identifies recorded, override, official, and catalog sources.

### Changed

- Reassessed pricing-source coverage for Claude Code and Codex subscription usage; direct OpenAI pricing remains deferred because local history lacks the route tier needed to select its published rates.

## [0.1.2] - Local HTML report

### Added

- Static HTML bundle with agent pages and local navigation.
- Collapsible, sortable, mobile-scrollable report tables.
- Token leaders in HTML, terminal, JSON, and Markdown summaries.

## [0.1.1] - Cost-status-safe breakdowns

### Added

- Provider-scoped model-ID rules and proxy-route provenance.

### Fixed

- Route, model, project, session, and daily breakdowns retain pricing status so unknown and non-metered usage renders as `N/A`, never as a free `$0` cost. Mixed known and unavailable cost is marked partial.

### Changed

- Terminal breakdowns use comparison tables. CSV and Markdown route exports include cost status; CSV emits `N/A` for unavailable known cost.

## [0.1.0] - Local observability, diagnostics, pacing, and stable exports

### Fixed

- Parse Claude Code date-only timestamps correctly; aggregate rates use observed dates and omit false daily spikes.
- Attribute Pi sessions from session headers, retaining the legacy per-event fallback.
- Calculate pacing from the full calendar budget window; provider budgets override agent budgets for their routes.

### Added

- Activity ratios for requests/turns/tool calls, reported as `N/A` when their inputs are absent.
- Read-only parser-drift and unreachable-source warnings; retained history is never deleted.
- `eurysx doctor` for terminal-only harness, source, pricing-cache, and configuration diagnostics. Failed-refresh report warnings are agent-scoped.
- Preference budgets, deterministic pacing, and exact unresolved-route diagnostics; subscription billing remains `N/A`.
- Stable JSON schema v1 plus CSV and Markdown exports from the shared analysis result.
- Operational manual with CLI/output redirects and a source-drift regression check.
- Trusted-Publishing GitHub workflow: validation, PyPI upload, and changelog-based GitHub Release.

### Tests

- Coverage for timestamp scope, Pi attribution, ratios, diagnostics, stored-source failures, and the doctor command.

## [0.0.5] - More hardening: pushdown, selectors, grouping, comparisons

### Added

- `--model` and `--provider` selectors, SQL WHERE filters in `store.events()`, combinable with `--agent` and the period selectors; NULL providers match `--provider unknown` (COALESCE).
- `--billing-mode` (metered|subscription|credit|quota|local|unknown), applied post-pricing via the `analyze_agent(billing_modes=...)` hook: billing_mode is a pricing-time artifact flipped to `metered` on recorded-cost conflict, so SQL cannot pre-filter it.
- `period_comparison`: bounded runs compare the current period against the same-length previous window (`_previous_window`, same filters) — terminal `PERIOD COMPARISON` (tokens, cost, entries, requests + Δ) and an additive JSON block; all-time runs omit it.

### Changed

- OpenCode timestamps stored as ISO-8601 (parser v3); epoch-millis rows re-collect on refresh.
- Claude Code aggregate scope warning now via a store presence check, so it survives SQL filtering.
- `store.events()` filters agents, dates, models, and providers in SQL; the Python date filter remains as the equivalence reference.
- Indices on provider, model, project, session (additive, idempotent).
- `AgentStats` gained `project_breakdown`/`session_breakdown` (model_breakdown shape; `unknown` bucket for unattributed rows), shown in terminal as `BREAKDOWN BY SESSION`/`BREAKDOWN BY PROJECT` and in JSON; unattributed-only sections print `No ... attribution available.`.

### Tests

- SQL-vs-Python equivalence, presence check, indices, grouping (incl. unattributed), two-period reuse/disjointness.
- Per-selector and combination tests incl. recorded-cost-flips-to-metered; previous-window math; simulated-CLI comparison asserting values and delta.
- Phase 1 baseline extended for additive keys `project_breakdown`, `session_breakdown`, `period_comparison`; Phase 6 pins the final contract.

## [0.0.4] - Report baseline + result seams

### Added

- Tests lock JSON `--output` schema and terminal report key sections against a hermetic fixture to assert exact values.
- Added cache read and cache efficiency ratios to JSON exports (previously terminal-only).
- `AnalysisReport` encapsulates period, per-agent stats/display periods, and pricing/preferences provenance; moved JSON assembly from CLI to `render.py`.
- `UsageAnalyzer.display_period` unit tests for ranged and all-time period derivations.

### Changed

- Terminal report, comparison summary, and JSON export now consume `AnalysisReport` instead of CLI-assembled payloads.
- Internal test-only models moved out of CLI exports; tests now import `eurysx.models` directly.

## [0.0.3] - Incremental collection + attribution and diagnostics

### Added

- Incremental per-source collection: collectors enumerate raw sources with stat fingerprints and a parser version; the CLI skips unchanged sources and transactionally replaces only sources that moved.
- Best-effort project attribution: Pi session-header `cwd`, Codex `session_meta.cwd`, and OpenCode's `session.directory` (when present) are stored per event as `project_id`; Claude Code aggregate rows stay unattributed.
- Failed source refreshes keep their last good events, record the error on the source row, and `report` warns when last-good data is being shown.

### Changed

- Default and `report` commands both read usage back from the store after collection; legacy per-agent bulk `collector:<agent>` store rows are purged.
- Collectors propagate read errors instead of printing and returning partial data. Pi, Codex, and OpenCode parser versions moved to 2.

## [0.0.2] - Local store implementation + module restructure

### Added

- Local SQLite usage storage with decimal-text recorded costs.
- `eurysx collect` and `eurysx report` workflows; stored reports do not collect agent history.

### Changed

- Source layout now uses the `src/eurysx` package
- CLI orchestration now delegates to focused collector, pricing, analysis, and rendering modules.

## [0.0.1] - Core CLI baseline

### Added

- `eurysx` CLI for Claude Code, OpenCode, Pi, and Codex local usage data.
- Token, request, turn, tool-call, model, provider, session, and pricing-source reporting.
- JSON reports, `--version`/`-v`, and explicit rolling, calendar, and ISO date selectors.
- Checkout-local pricing config/cache paths with explicit environment overrides.
- Configurable pricing resolution: recorded cost, overrides, enabled sources, cache fallback, then unknown.
- User-owned per-agent route and billing preferences with pricing-provider mapping.
- Ordered provider-aware pricing source fallbacks and a project-local cache.
- Sanitized collector fixtures, pricing regression tests, setuptools metadata, and MIT license.

### Changed

- Reports label money as known cost and expose unknown-cost tokens and priced-token coverage.
- Selected date ranges exclude `Claude Code`'s aggregate stats-cache data and emit a scope warning.
- Source distributions include the changelog and safe pricing configuration sample.
- Empty-token coverage is `N/A`; malformed pricing configuration is reported as a diagnostic.
- `OpenCode` turn detection no longer depends on SQLite message-row order.
- All-time reports derive cost-rate periods from observed usage dates.
- Reports separate metered token coverage from subscription, credit, quota, local, and unknown usage.
- Codex preserves recorded route-provider metadata; provider-qualified prices no longer cross-match.
- Preferences now use agent defaults and provider overrides; `aws-bedrock` is renamed to `amazon-bedrock` in pricing configuration and cache provenance.
- Configuration documentation and samples now distinguish required enabled-source settings from optional agent and provider policies.
- OpenCode collector parity now uses a tracked, sanitized SQL fixture that builds its temporary SQLite database during tests.
