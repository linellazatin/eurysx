# Changelog

## [0.0.3] - Incremental collection + attribution and diagnostics

### Added

- Incremental per-source collection: collectors enumerate raw sources with stat
  fingerprints and a parser version; the CLI skips unchanged sources and
  transactionally replaces only sources that moved.
- Best-effort project attribution: Pi session-header `cwd`, Codex
  `session_meta.cwd`, and OpenCode's `session.directory` (when present) are
  stored per event as `project_id`; Claude Code aggregate rows stay unattributed.
- Failed source refreshes keep their last good events, record the error on the
  source row, and `report` warns when last-good data is being shown.

### Changed

- Default and `report` commands both read usage back from the store after
  collection; legacy per-agent bulk `collector:<agent>` store rows are purged.
- Collectors propagate read errors instead of printing and returning partial
  data. Pi, Codex, and OpenCode parser versions moved to 2.

## [0.0.2] - Local store implementation + module restructure

### Added

- Local SQLite usage storage with decimal-text recorded costs.
- `eurysx collect` and `eurysx report` workflows; stored reports do not collect
  agent history.

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
- Preferences now use agent defaults and provider overrides; `aws-bedrock` is
  renamed to `amazon-bedrock` in pricing configuration and cache provenance.
- Configuration documentation and samples now distinguish required enabled-source
  settings from optional agent and provider policies.
- OpenCode collector parity now uses a tracked, sanitized SQL fixture that builds
  its temporary SQLite database during tests.
