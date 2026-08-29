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
  timestamp, and session identity where the harness records it.
- [x] Make unknown cost visibly unknown in terminal and JSON output; distinguish
  known cost coverage from a genuinely zero-cost model.
- [x] Define rolling versus calendar date semantics and document the CLI contract.
- [x] Define a user-writable configuration and cache location policy for installed
  Eurysx, then package the required safe defaults.
- [x] Add an explicit license file matching package metadata.
- [x] Ignore generated package artifacts and conventional local reports.
- [x] Include the pricing sample and changelog in the source distribution, then
  verify its installed CLI and documentation paths.
- [x] Render priced-token coverage as not applicable when no token entries exist.
- [x] Treat malformed pricing configuration values as diagnostics rather than
  startup exceptions.
- [x] Make OpenCode turn detection independent of SQLite message-row order.
- [x] Add a release check that the CLI and package metadata versions match.
- [ ] Review the Act I release checklist, then create the user-owned v0.0.1
  commit and tag.

## Act II: Continuity

Make analysis incremental, reproducible, and privacy-preserving.

- [ ] Split the Act I module only when this Act begins, retaining its collector
  fixtures as the compatibility contract.
- [ ] Move the CLI, shared event/path helpers, pricing, analysis, and output
  into focused modules; place the four harness adapters in
  `collectors/{claude_code,codex,opencode,pi}.py`.
- [ ] Keep CLI agent selection as a fixed mapping. Do not add a collector
  registry, plugin system, or abstract collector protocol until a real caller
  needs one.
- [ ] Keep each collector limited to discovery and metadata normalization;
  pricing, storage, analysis, and rendering remain downstream.
- [ ] Define one canonical usage-event envelope with event type, stable identity,
  session/project identifiers, and source provenance.
- [ ] Choose the durable money representation before storing aggregated cost.
- [ ] Add a local SQLite store for normalized metadata only.
- [ ] Add idempotent collection, source cursors, and safe deduplication.
- [ ] Record collector/parser version diagnostics for changing harness formats.
- [ ] Add project and session attribution where source metadata supports it.
- [ ] Read reports from collected local history without rescanning every source.

## Act III: Clarity

Make Eurysx useful for decisions and automation.

- [ ] Return structured analysis results before terminal rendering.
- [ ] Add stable JSON, CSV, and Markdown export contracts.
- [ ] Add explicit date ranges, calendar periods, agent/model/provider filters,
  grouping, and comparisons.
- [ ] Report known cost, unknown-cost coverage, cache ratios, request/turn/tool
  ratios, and project/session costs where available.
- [ ] Add `doctor` diagnostics for detected harnesses, pricing state, and
  collector compatibility.
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
  conversation analysis, agent benchmarking, and subscription-value estimates.
