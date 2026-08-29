# Changelog

## [0.0.1] - Unreleased

### Added

- `eurysx` CLI for Claude Code, OpenCode, Pi, and Codex local usage data.
- Token, request, turn, tool-call, model, provider, session, and pricing-source reporting.
- JSON reports, `--version`/`-v`, and explicit rolling, calendar, and ISO date selectors.
- User-writable pricing config/cache paths with XDG, macOS, Windows, and environment overrides.
- Configurable pricing resolution: recorded cost, overrides, enabled sources, cache fallback, then unknown.
- Sanitized collector fixtures, pricing regression tests, setuptools metadata, and MIT license.

### Changed

- Reports label money as known cost and expose unknown-cost tokens and priced-token coverage.
- Selected date ranges exclude `Claude Code`'s aggregate stats-cache data and emit a scope warning.
- Source distributions include the changelog and safe pricing configuration sample.
- Empty-token coverage is `N/A`; malformed pricing configuration is reported as a diagnostic.
- `OpenCode` turn detection no longer depends on SQLite message-row order.
- All-time reports derive cost-rate periods from observed usage dates.
