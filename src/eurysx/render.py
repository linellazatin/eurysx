"""Report rendering: terminal presentation and the JSON export payload."""

import csv
import html
import io
import sys
from typing import Dict

from .models import AgentStats, AnalysisReport


class Colors:
    """ANSI color codes for terminal output."""
    # Agent-specific colors
    claude_code = "\033[38;5;208m"  # Orange
    opencode = "\033[38;5;45m"      # Cyan
    pi = "\033[38;5;129m"          # Red/Pink
    codex = "\033[38;5;228m"       # Yellow
    reset = "\033[0m"
    
    @classmethod
    def disable(cls):
        """Disable colors (for file output or non-terminal)."""
        for attr in dir(cls):
            if not attr.startswith('_') and attr != 'disable':
                setattr(cls, attr, "")


def should_colorize() -> bool:
    """Check if we should use colors (not outputting to file, terminal supports it)."""
    return sys.stdout.isatty()

AGENT_COLORS = {
    'claude-code': Colors.claude_code,
    'opencode': Colors.opencode,
    'pi': Colors.pi,
    'codex': Colors.codex,
}

AGENT_NAMES = {
    'claude-code': 'CLAUDE CODE',
    'opencode': 'OPENCODE',
    'pi': 'PI CODING AGENT',
    'codex': 'CODEX',
}


def _cost_status(data: Dict) -> str:
    counts = data.get("cost_status_counts", {})
    known = (counts.get("recorded", 0) + counts.get("configured", 0)
             + counts.get("estimated", 0))
    if known and (counts.get("unknown") or counts.get("not_applicable")):
        return "partial"
    if known:
        return "known"
    if counts.get("unknown"):
        return "unknown"
    return "not applicable"


def _cost_display(data: Dict) -> str:
    status = _cost_status(data)
    if status in ("unknown", "not applicable"):
        return "N/A"
    cost = f"${data['cost']:,.6f}"
    return f"{cost} (partial)" if status == "partial" else cost


def _lane_display(data: Dict, name: str) -> str:
    value = data.get(name)
    return "N/A" if value is None else f"${value:,.6f}"


def _comparison_leaders(report: AnalysisReport) -> Dict:
    """Return deterministic token leaders from the existing route breakdowns."""
    agents = {}
    combined = {}
    for agent, stats in sorted(report.agent_stats.items()):
        routes = []
        for route, data in stats.route_breakdown.items():
            provider_model, _ = route.rsplit(" [", 1)
            provider, model = provider_model.split("/", 1)
            leader = {"provider": provider, "model": model, "tokens": data["tokens"]}
            routes.append(leader)
            combined[(provider, model)] = combined.get((provider, model), 0) + data["tokens"]
        if routes:
            agents[agent] = min(routes, key=lambda item: (-item["tokens"], item["provider"], item["model"]))
    return {
        "agents": agents,
        "combined": [
            {"provider": provider, "model": model, "tokens": tokens}
            for (provider, model), tokens in sorted(combined.items(), key=lambda item: (-item[1], *item[0]))[:3]
        ],
    }


def _print_table(headers, rows):
    rows = [[str(value) for value in row] for row in rows]
    widths = [max([len(header), *(len(row[index]) for row in rows)]) for index, header in enumerate(headers)]
    print("  ".join(f"{header:<{width}}" for header, width in zip(headers, widths)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(f"{value:<{width}}" for value, width in zip(row, widths)))


def _print_metrics(rows):
    rows = [(f"{label}:", str(value)) for label, value in rows]
    label_width = max(len(label) for label, _ in rows)
    value_width = max(len(value) for _, value in rows)
    for label, value in rows:
        print(f"{label:<{label_width}}  {value:>{value_width}}")


def _print_grouped_section(title: str, breakdown: Dict, attribution_noun: str):
    """Terminal breakdown by session or project; 'unknown' bucket covers unattributed rows."""
    print(f"\n{'=' * 80}")
    print(title)
    print('=' * 80)
    if len(breakdown) == 1 and "unknown" in breakdown:
        print(f"\nNo {attribution_noun} attribution available.")
        return
    rows = sorted(breakdown.items(), key=lambda item: item[1]['cost'], reverse=True)
    _print_table(
        (attribution_noun.title(), "Tokens", "Known cost", "Actual recorded", "API-equivalent estimate", "Cost status", "Requests", "Turns", "Tool calls"),
        [
            (
                key,
                f"{data['input'] + data['output'] + data['cache_read'] + data['cache_write']:,}",
                _cost_display(data), _lane_display(data, "actual_cost"), _lane_display(data, "api_equivalent_estimate"), _cost_status(data),
                f"{data['model_requests']:,}", f"{data['model_turns']:,}", f"{data['model_tool_calls']:,}",
            )
            for key, data in rows
        ],
    )


def print_agent_header(agent: str, title: str = "USAGE ANALYSIS"):
    """Print color-coded agent header."""
    color = AGENT_COLORS.get(agent, Colors.reset)
    name = AGENT_NAMES.get(agent, agent.upper())
    
    print(f"{color}{'=' * 80}{Colors.reset}")
    print(f"{color}{name} {title}{Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")


def print_single_agent_report(report: AnalysisReport, agent: str):
    """Print detailed terminal report for a single agent from the structured result."""
    color = AGENT_COLORS.get(agent, Colors.reset)
    stats = report.agent_stats[agent]
    display = report.agent_displays[agent]
    
    # Print header with color
    print_agent_header(agent)
    
    print(f"\n{color}Analysis Period: {display.start_date} to {display.end_date} ({display.label}){Colors.reset}")
    for warning in stats.scope_warnings:
        print(f"Warning: {warning}")
    
    if stats.usage_entries == 0:
        print(f"\n{color}No usage data found for {AGENT_NAMES.get(agent, agent.upper())}{Colors.reset}")
        return
    
    print(f"\n{color}Found {stats.usage_entries} usage entries{Colors.reset}")
    
    # ===== TOTAL USAGE SECTION =====
    print(f"\n{color}{'=' * 80}{Colors.reset}")
    print(f"{color}TOTAL USAGE (ALL MODELS){Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")
    
    print()
    _print_metrics((
        ("Model requests", f"{stats.total_model_requests:,}"),
        ("Model turns", f"{stats.total_model_turns:,}"),
        ("Model tool calls", f"{stats.total_model_tool_calls:,}"),
        ("Input tokens", f"{stats.total_input_tokens:,}"),
        ("Output tokens", f"{stats.total_output_tokens:,}"),
        ("Cache read tokens", f"{stats.total_cache_read_tokens:,}"),
        ("Cache creation tokens", f"{stats.total_cache_write_tokens:,}"),
        ("GRAND TOTAL TOKENS", f"{stats.total_tokens:,}"),
        ("KNOWN COST", f"${stats.known_cost:,.6f}"),
        ("Actual recorded cost", f"${stats.actual_cost:,.6f}" if stats.cost_status_counts.get("recorded") else "N/A"),
        ("API-equivalent estimate", f"${stats.api_equivalent_estimate:,.6f}" if stats.estimate_status_counts.get("estimated") else "N/A"),
    ))
    
    # ===== BREAKDOWN BY MODEL SECTION =====
    print(f"\n{color}{'=' * 80}{Colors.reset}")
    print(f"{color}BREAKDOWN BY MODEL{Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")
    
    sorted_models = sorted(
        stats.model_breakdown.items(),
        key=lambda x: x[1]['cost'],
        reverse=True
    )
    
    _print_table(
        ("Model", "Tokens", "Known cost", "Actual recorded", "API-equivalent estimate", "Cost status", "Requests", "Turns", "Tool calls"),
        [
            (
                model_id,
                f"{model_data['input'] + model_data['output'] + model_data['cache_read'] + model_data['cache_write']:,}",
                _cost_display(model_data), _lane_display(model_data, "actual_cost"), _lane_display(model_data, "api_equivalent_estimate"), _cost_status(model_data),
                f"{model_data['model_requests']:,}",
 f"{model_data['model_turns']:,}",
                f"{model_data['model_tool_calls']:,}",
            )
            for model_id, model_data in sorted_models
        ],
    )

    _print_grouped_section("BREAKDOWN BY SESSION", stats.session_breakdown, "session")
    _print_grouped_section("BREAKDOWN BY PROJECT", stats.project_breakdown, "project")
    
    # ===== COST PROJECTIONS PER TIME PERIOD =====
    print(f"\n{color}{'=' * 80}{Colors.reset}")
    print(f"{color}COST PROJECTIONS PER TIME PERIOD{Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")
    
    days_active = len(stats.daily_activity)
    total_days = (display.end_date - display.start_date).days + 1
    # Aggregate-only agents (claude-code) have no per-day rows: say so instead
    # of repeating the across-all-days figure under an "active days" label.
    active_cost = f"${stats.total_cost / days_active:,.6f}" if days_active else "n/a"
    active_tokens = f"{stats.total_tokens / days_active:,.0f}" if days_active else "n/a"

    print()
    _print_metrics((
        (f"Daily (across all {total_days} days)", f"${stats.daily_cost:,.6f}"),
        (f"Daily (active days only, {days_active} days)", active_cost),
        (f"Weekly (across {total_days / 7:.1f} weeks)", f"${stats.weekly_cost:,.6f}"),
        (f"Monthly (30-day avg, {total_days / 30:.1f} months)", f"${stats.monthly_cost:,.6f}"),
        (f"Quarterly (90-day avg, {total_days / 90:.1f} quarters)", f"${stats.quarterly_cost:,.6f}"),
        (f"Yearly (365-day avg, {total_days / 365:.2f} years)", f"${stats.yearly_cost:,.6f}"),
    ))
    
    # ===== TOKEN VOLUME PER TIME PERIOD =====
    print(f"\n{color}{'=' * 80}{Colors.reset}")
    print(f"{color}TOKEN VOLUME PER TIME PERIOD{Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")
    
    token_daily_avg = stats.total_tokens / total_days if total_days > 0 else 0
    token_weekly = token_daily_avg * 7
    token_monthly = token_daily_avg * 30
    token_quarterly = token_daily_avg * 90
    token_yearly = token_daily_avg * 365
    
    print()
    _print_metrics((
        ("Daily (across all days)", f"{token_daily_avg:,.0f} tokens"),
        ("Daily (active days only)", f"{active_tokens} tokens"),
        ("Weekly", f"{token_weekly:,.0f} tokens"),
        ("Monthly (30-day avg)", f"{token_monthly:,.0f} tokens"),
        ("Quarterly (90-day avg)", f"{token_quarterly:,.0f} tokens"),
        ("Yearly (365-day avg)", f"{token_yearly:,.0f} tokens"),
    ))

    # ===== MODEL ACTIVITY VOLUME PER TIME PERIOD =====
    print(f"\n{color}{'=' * 80}{Colors.reset}")
    print(f"{color}MODEL ACTIVITY VOLUME PER TIME PERIOD{Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")

    activity_periods = (
        ('Daily (across all days):', 1),
        ('Weekly:', 7),
        ('Monthly (30-day avg):', 30),
        ('Quarterly (90-day avg):', 90),
        ('Yearly (365-day avg):', 365),
    )
    activity_daily = {
        'requests': stats.total_model_requests / total_days if total_days > 0 else 0,
        'turns': stats.total_model_turns / total_days if total_days > 0 else 0,
        'tool_calls': stats.total_model_tool_calls / total_days if total_days > 0 else 0,
    }

    print(f"\n{'Period':<28} {'Requests':>15} {'Turns':>15} {'Tool calls':>15}")
    print('-' * 76)
    for label, multiplier in activity_periods:
        print(
            f"{label:<28} "
            f"{activity_daily['requests'] * multiplier:>15,.0f} "
            f"{activity_daily['turns'] * multiplier:>15,.0f} "
            f"{activity_daily['tool_calls'] * multiplier:>15,.0f}"
        )

    ratios = []
    if stats.requests_per_turn is not None:
        ratios.append(f"requests/turn: {stats.requests_per_turn:.2f}")
    if stats.tool_calls_per_request is not None:
        ratios.append(f"tool calls/request: {stats.tool_calls_per_request:.2f}")
    if stats.tool_calls_per_turn is not None:
        ratios.append(f"tool calls/turn: {stats.tool_calls_per_turn:.2f}")
    print(
        f"\nRatios: {', '.join(ratios) if ratios else 'N/A (no request, turn, or tool rows in scope)'}"
    )

    # ===== DAILY ACTIVITY =====
    print(f"\n{color}{'=' * 80}{Colors.reset}")
    print(f"{color}DAILY ACTIVITY{Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")
    if stats.daily_activity:
        _print_table(
            ("Date", "Tokens", "Known cost", "Cost status"),
            [
                (activity_date, f"{data['tokens']:,}", _cost_display(data), _cost_status(data))
                for activity_date, data in sorted(stats.daily_activity.items())
            ],
        )
    else:
        print("No daily activity data available.")
    
    # ===== SUMMARY STATISTICS =====
    print(f"\n{color}{'=' * 80}{Colors.reset}")
    print(f"{color}SUMMARY STATISTICS{Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")
    
    print()
    _print_metrics((
        ("Total sessions", f"{stats.sessions_count}" + (" attributed (+ unattributed rows)" if "unknown" in stats.session_breakdown else "")),
        ("Total messages (usage entries)", f"{stats.usage_entries:,}"),
        ("Unique models used", f"{len(stats.unique_models):,}"),
    ))
    
    # ===== CACHE EFFECTIVENESS =====
    print(f"\n{color}{'=' * 80}{Colors.reset}")
    print(f"{color}CACHE EFFECTIVENESS{Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")
    
    total_cache_tokens = stats.total_cache_read_tokens + stats.total_cache_write_tokens
    cache_rows = []
    if stats.cache_read_ratio is not None:
        cache_rows.append(("Cache read ratio", f"{stats.cache_read_ratio:.1%} ({stats.total_cache_read_tokens:,} / {total_cache_tokens:,})"))
    if stats.cache_efficiency_ratio is not None:
        cache_rows.append(("Cache efficiency ratio", f"{stats.cache_efficiency_ratio:.1f}:1"))
    if cache_rows:
        _print_metrics(cache_rows)
    
    # ===== COST ANALYSIS =====
    print(f"\n{color}{'=' * 80}{Colors.reset}")
    print(f"{color}COST ANALYSIS{Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")
    
    actual_cost = stats.known_cost
    coverage = f"{stats.priced_token_coverage:.1%}" if stats.priced_token_coverage is not None else "N/A"
    cost_rows = [
        ("Legacy known cost", f"${actual_cost:,.6f}"),
        ("Actual recorded cost", f"${stats.actual_cost:,.6f}" if stats.cost_status_counts.get("recorded") else "N/A"),
        ("API-equivalent estimate", f"${stats.api_equivalent_estimate:,.6f}" if stats.estimate_status_counts.get("estimated") else "N/A"),
        ("Unknown-cost entries", f"{stats.unknown_cost_count:,}"),
        ("Unknown metered-cost tokens", f"{stats.unknown_cost_tokens:,}"),
        ("Metered token coverage", coverage),
    ]
    cost_rows.extend((f"{billing_mode.title()} tokens", f"{tokens:,}") for billing_mode, tokens in sorted(stats.non_metered_tokens.items()))
    print()
    _print_metrics(cost_rows)
    if stats.unresolved_routes:
        print("\nUnresolved metered routes:")
        _print_table(
            ("Provider", "Model", "Tokens", "Reason"),
            [
                (route["provider"], route["model"], f"{route['tokens']:,}", route["reason"])
                for route in stats.unresolved_routes
            ],
        )
    for route, pacing in stats.pacing.items():
        if pacing["status"] == "unavailable":
            print(f"Budget pacing ({route}): unavailable ({pacing['reason']})")
        else:
            print(f"Budget pacing ({route}): {pacing['status']} (${pacing['spent_usd']:.2f} / ${pacing['budget_usd']:.2f})")
    print("\nRoute breakdown:")
    route_rows = []
    for route, route_data in sorted(stats.route_breakdown.items()):
        provider_model, mode = route.rsplit(" [", 1)
        provider, model = provider_model.split("/", 1)
        route_rows.append((
            provider, ", ".join(route_data.get("observed_providers", [])), model, mode[:-1], f"{route_data['tokens']:,}",
            _cost_display(route_data), _lane_display(route_data, "actual_cost"), _lane_display(route_data, "api_equivalent_estimate"), _cost_status(route_data), f"{route_data['entries']:,}",
        ))
    _print_table(
        ("Provider", "Observed via", "Model", "Billing mode", "Tokens", "Known cost", "Actual recorded", "API-equivalent estimate", "Cost status", "Entries"),
        route_rows,
    )
    source_paths = {
        "amazon-bedrock": "Eurysx cache/pricing-amazon-bedrock.json",
        "pi-models-store": "Eurysx cache/pricing-pi-models-store.json",
        "models-dev": "Eurysx cache/pricing-models-dev.json",
        "override": "Eurysx config/pricing.jsonc",
        "recorded": "recorded usage data",
    }
    pricing_sources = sorted(stats.pricing_sources) or ["unknown"]
    source_details = ", ".join(
        f"{source} ({source_paths.get(source, 'no cache path')})"
        for source in pricing_sources
    )
    print("=" * 80)
    print(f"Model Pricing Source: {source_details}")
    comparison = report.period_comparison.get(agent)
    if not comparison:
        return
    print(f"\n{'=' * 80}")
    print("PERIOD COMPARISON")
    print("=" * 80)
    current = comparison["current"]
    previous = comparison["previous"]

    def delta(current_value, previous_value):
        if previous_value == 0:
            return "n/a" if current_value == 0 else "new"
        return f"{(current_value - previous_value) / previous_value * 100:+.0f}%"

    def format_tokens(value):
        return f"{value:,}"

    def format_cost(value):
        return f"${value:,.6f}"

    rows = (
        ("Total tokens", current["total_tokens"], previous["total_tokens"], format_tokens),
        ("Known cost", current["known_cost"], previous["known_cost"], format_cost),
        ("Usage entries", current["usage_entries"], previous["usage_entries"], format_tokens),
        ("Model requests", current["model_requests"], previous["model_requests"], format_tokens),
    )
    print(f"\n{'':<18} {'Current':>20} {'Previous':>20} {'Δ':>10}")
    print("-" * 70)
    for label, current_value, previous_value, format_value in rows:
        print(
            f"{label:<18} {format_value(current_value):>20} "
            f"{format_value(previous_value):>20} {delta(current_value, previous_value):>10}"
        )
    print(f"\n{comparison['current_period']} vs {comparison['previous_period']}")


def print_summary_comparison(report: AnalysisReport):
    """Print comparison summary across multiple agents from the structured result."""
    # Use reset colors for comparison (header is already colored per-agent)
    print(f"\n{'=' * 80}")
    print("COMPARISON SUMMARY")
    print("=" * 80)
    
    print(f"\n{'Agent':<15} {'Total Tokens':>15} {'Requests':>12} {'Turns':>12} {'Tool Calls':>12} {'Known Cost':>15} {'Daily Known':>15}")
    print("-" * 105)
    
    combined_tokens = 0
    combined_requests = 0
    combined_turns = 0
    combined_tool_calls = 0
    combined_cost = 0.0
    
    for agent, stats in report.agent_stats.items():
        if stats.usage_entries > 0:
            agent_name = agent.replace('-', ' ').title()
            print(
                f"{agent_name:<15} {stats.total_tokens:>15,} "
                f"{stats.total_model_requests:>12,} {stats.total_model_turns:>12,} "
                f"{stats.total_model_tool_calls:>12,} ${stats.total_cost:>14,.2f} "
                f"${stats.daily_cost:>14,.2f}"
            )
            combined_tokens += stats.total_tokens
            combined_requests += stats.total_model_requests
            combined_turns += stats.total_model_turns
            combined_tool_calls += stats.total_model_tool_calls
            combined_cost += stats.total_cost
        else:
            print(f"{agent.replace('-', ' ').title():<15} {'No data':>15} {'-':>12} {'-':>12} {'-':>12} {'-':>15} {'-':>15}")
    
    print("-" * 105)
    print(
        f"{'COMBINED TOTAL':<15} {combined_tokens:>15,} "
        f"{combined_requests:>12,} {combined_turns:>12,} {combined_tool_calls:>12,} "
        f"${combined_cost:>14,.2f}"
    )
    print("=" * 105)
    leaders = _comparison_leaders(report)
    print("\nTOKEN LEADERS")
    _print_table(
        ("Agent", "Provider", "Model", "Tokens"),
        [
            (AGENT_NAMES.get(agent, agent), leader["provider"], leader["model"], f"{leader['tokens']:,}")
            for agent, leader in leaders["agents"].items()
        ],
    )
    print("\nCOMBINED TOP 3")
    _print_table(
        ("Rank", "Provider", "Model", "Tokens"),
        [
            (index, leader["provider"], leader["model"], f"{leader['tokens']:,}")
            for index, leader in enumerate(leaders["combined"], 1)
        ],
    )


def _agent_stats_dict(stats: AgentStats) -> Dict:
    """Serializable per-agent stats block for the JSON report."""
    return {
        "model_requests": stats.total_model_requests,
        "model_turns": stats.total_model_turns,
        "model_tool_calls": stats.total_model_tool_calls,
        "total_input_tokens": stats.total_input_tokens,
        "total_output_tokens": stats.total_output_tokens,
        "total_cache_read_tokens": stats.total_cache_read_tokens,
        "total_cache_write_tokens": stats.total_cache_write_tokens,
        "total_tokens": stats.total_tokens,
        "total_cost": stats.total_cost,
        "known_cost": stats.known_cost,
        "actual_cost_usd": stats.actual_cost,
        "api_equivalent_estimate_usd": stats.api_equivalent_estimate,
        "estimate_status_counts": stats.estimate_status_counts,
        "estimate_entries": stats.estimate_entries,
        "unknown_cost_count": stats.unknown_cost_count,
        "unknown_cost_tokens": stats.unknown_cost_tokens,
        "priced_token_coverage": stats.priced_token_coverage,
        "cache_read_ratio": stats.cache_read_ratio,
        "cache_efficiency_ratio": stats.cache_efficiency_ratio,
        "requests_per_turn": stats.requests_per_turn,
        "tool_calls_per_request": stats.tool_calls_per_request,
        "tool_calls_per_turn": stats.tool_calls_per_turn,
        "metered_tokens": stats.metered_tokens,
        "non_metered_tokens": stats.non_metered_tokens,
        "billing_mode_tokens": stats.billing_mode_tokens,
        "route_breakdown": stats.route_breakdown,
        "cost_status_counts": stats.cost_status_counts,
        "pricing_sources": sorted(stats.pricing_sources),
        "pricing_source_kinds": stats.pricing_source_kinds,
        "pricing_fetched_at": stats.pricing_fetched_at,
        "daily_cost": stats.daily_cost,
        "weekly_cost": stats.weekly_cost,
        "monthly_cost": stats.monthly_cost,
        "quarterly_cost": stats.quarterly_cost,
        "yearly_cost": stats.yearly_cost,
        "usage_entries": stats.usage_entries,
        "sessions_count": stats.sessions_count,
        "unique_models": sorted(stats.unique_models),
        "model_breakdown": stats.model_breakdown,
        "daily_activity": stats.daily_activity,
        "scope_warnings": stats.scope_warnings,
        "unresolved_routes": stats.unresolved_routes,
        "pacing": stats.pacing,
        "project_breakdown": stats.project_breakdown,
        "session_breakdown": stats.session_breakdown,
    }


def build_csv_report(report: AnalysisReport) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("agent", "provider", "observed_providers", "model", "billing_mode", "tokens", "known_cost_usd", "actual_recorded_cost_usd", "api_equivalent_estimate_usd", "entries", "model_requests", "model_turns", "model_tool_calls", "cost_status"))
    for agent, stats in sorted(report.agent_stats.items()):
        for route, data in sorted(stats.route_breakdown.items()):
            provider_model, mode = route.rsplit(" [", 1)
            provider, model = provider_model.split("/", 1)
            status = _cost_status(data)
            cost = data["cost"] if status in ("known", "partial") else "N/A"
            writer.writerow((agent, provider, ",".join(data.get("observed_providers", [])), model, mode[:-1], data["tokens"], cost, data["actual_cost"] if data["actual_cost"] is not None else "N/A", data["api_equivalent_estimate"] if data["api_equivalent_estimate"] is not None else "N/A", data["entries"], data["model_requests"], data["model_turns"], data["model_tool_calls"], status))
    return output.getvalue()


def build_markdown_report(report: AnalysisReport) -> str:
    lines = ["# Eurysx report", "", f"Period: {report.period_label}", "", "## Token leaders", "", "| Agent | Provider | Model | Tokens |", "| --- | --- | --- | ---: |"]
    leaders = _comparison_leaders(report)
    for agent, leader in leaders["agents"].items():
        lines.append(f"| {agent} | {leader['provider']} | {leader['model']} | {leader['tokens']:,} |")
    lines += ["", "### Combined top 3", "", "| Rank | Provider | Model | Tokens |", "| ---: | --- | --- | ---: |"]
    for index, leader in enumerate(leaders["combined"], 1):
        lines.append(f"| {index} | {leader['provider']} | {leader['model']} | {leader['tokens']:,} |")
    for agent, stats in sorted(report.agent_stats.items()):
        lines += ["", f"## {agent}", "", f"Tokens: {stats.total_tokens:,}", f"Legacy known cost: ${stats.known_cost:.6f}", f"Actual recorded cost: ${stats.actual_cost:.6f}" if stats.cost_status_counts.get("recorded") else "Actual recorded cost: N/A", f"API-equivalent estimate: ${stats.api_equivalent_estimate:.6f}" if stats.estimate_status_counts.get("estimated") else "API-equivalent estimate: N/A", "", "| Provider | Observed via | Model | Billing mode | Tokens | Known cost | Actual recorded | API-equivalent estimate | Cost status |", "| --- | --- | --- | --- | ---: | ---: | ---: | --- |"]
        for route, data in sorted(stats.route_breakdown.items()):
            provider_model, mode = route.rsplit(" [", 1)
            provider, model = provider_model.split("/", 1)
            lines.append(f"| {provider} | {', '.join(data.get('observed_providers', []))} | {model} | {mode[:-1]} | {data['tokens']:,} | {_cost_display(data)} | {_lane_display(data, 'actual_cost')} | {_lane_display(data, 'api_equivalent_estimate')} | {_cost_status(data)} |")
    return "\n".join(lines) + "\n"


def build_html_reports(report: AnalysisReport) -> Dict[str, str]:
    """Render a self-contained local HTML bundle from one analysis result."""
    def text(value) -> str:
        return html.escape(str(value))

    def table(headers, rows, sortable=False) -> str:
        header = (
            f'<th aria-sort="none" role="button" tabindex="0">{text(value)}</th>'
            if sortable else f"<th>{text(value)}</th>"
            for value in headers
        )
        content = (
            ('<table class="sortable">' if sortable else "<table>") + "<thead><tr>"
            + "".join(header) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{text(value)}</td>" for value in row) + "</tr>" for row in rows)
            + "</tbody></table>"
        )
        return f'<div class="table-scroll">{content}</div>' if sortable else content

    def section(title, content, open=False, meta=None) -> str:
        label = text(title) + (f'<span class="section-meta">{text(meta)}</span>' if meta else "")
        return f"<details{' open' if open else ''}><summary>{label}</summary>{content}</details>"

    def cost(stats, value) -> str:
        return _cost_display({"cost": value, "cost_status_counts": stats.cost_status_counts})

    def cost_context(stats) -> str:
        modes = sorted(stats.non_metered_tokens)
        if modes:
            return "Incremental cost unavailable for " + " and ".join(modes) + " usage."
        status = _cost_status({"cost_status_counts": stats.cost_status_counts})
        if status == "partial":
            return "Known cost excludes unavailable routes."
        if status == "unknown":
            return "Metered cost could not be resolved."
        return "All displayed costs are known."

    agents = sorted(report.agent_stats.items())
    nav = [("SUMMARY", "index.html"), *[(AGENT_NAMES.get(agent, agent), f"{agent}.html") for agent, _ in agents]]

    def page(title, active, body) -> str:
        links = "".join(
            f'<a href="{href}"' + (' aria-current="page"' if href == active else '') + f">{text(label)}</a>"
            for label, href in nav
        )
        return """<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>""" + text(title) + """</title><style>:root{--ink:#0c1520;--panel:#132334;--line:#31465a;--text:#e7eef5;--muted:#9fb0c1;--accent:#69d2e7;--signal:#f2bc5e}body{margin:0;background:var(--ink);color:var(--text);font:15px system-ui,sans-serif}.layout{display:grid;grid-template-columns:220px minmax(0,1fr);max-width:1400px;margin:auto}aside{padding:2rem 1rem;background:var(--panel);min-height:100vh}aside a{display:block;padding:.6rem;color:var(--muted);text-decoration:none;border-left:2px solid transparent}aside a[aria-current=page]{color:var(--accent);border-color:var(--accent)}main{padding:2rem;min-width:0}h1{margin:.15rem 0;color:var(--accent);letter-spacing:-.03em}.eyebrow{margin:0;color:var(--muted);font-size:.75rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase}.summary-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;margin:1.5rem 0;background:var(--line);border:1px solid var(--line)}.insight{padding:1rem;background:var(--panel)}.insight strong,.insight span{display:block}.insight strong{margin:.3rem 0;font-size:1.15rem}.insight span{color:var(--muted)}section,details{margin:1rem 0}summary{display:flex;justify-content:space-between;gap:1rem;padding:.75rem 0;color:var(--accent);font-size:1.1rem;font-weight:700;cursor:pointer}.section-meta{color:var(--muted);font-size:.8rem;font-weight:400}table{width:100%;border-collapse:collapse}th,td{padding:.55rem;text-align:left;border-bottom:1px solid var(--line)}th{color:var(--muted);font-size:.8rem;text-transform:uppercase}table.sortable th{cursor:pointer}.table-scroll{max-width:100%;overflow-x:auto}table.sortable{min-width:650px}th:focus,summary:focus{outline:2px solid var(--signal);outline-offset:2px}td{font-variant-numeric:tabular-nums}@media(max-width:700px){.layout{display:block}aside{min-height:auto}main{padding:1.25rem}.summary-grid{grid-template-columns:1fr}summary{font-size:1rem}}</style></head><body><div class=\"layout\"><aside><strong>Eurysx</strong>""" + links + "</aside><main>" + body + "</main></div><script>document.querySelectorAll('table.sortable').forEach(table=>{const headers=table.querySelectorAll('th');const value=cell=>{const text=cell.textContent.trim();if(!text||text==='N/A')return null;if(/^\\d{4}-\\d{2}-\\d{2}$/.test(text))return text;const number=text.replace(/[$,%]/g,'').match(/^-?[\\d,.]+/);return number?Number(number[0].replace(/,/g,'')):text.toLowerCase()};const sort=index=>{const header=headers[index];const ascending=header.getAttribute('aria-sort')!=='ascending';headers.forEach(item=>item.setAttribute('aria-sort','none'));header.setAttribute('aria-sort',ascending?'ascending':'descending');[...table.tBodies[0].rows].sort((left,right)=>{const a=value(left.cells[index]),b=value(right.cells[index]);if(a===null)return 1;if(b===null)return -1;return(typeof a==='number'&&typeof b==='number'?a-b:String(a).localeCompare(String(b)))*(ascending?1:-1)}).forEach(row=>table.tBodies[0].append(row))};headers.forEach((header,index)=>{header.addEventListener('click',()=>sort(index));header.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();sort(index)}})})});</script></body></html>"

    overview_rows = []
    combined = {"tokens": 0, "requests": 0, "turns": 0, "tools": 0, "cost": 0.0, "daily": 0.0, "counts": {}}
    for agent, stats in agents:
        overview_rows.append((AGENT_NAMES.get(agent, agent), f"{stats.total_tokens:,}",
                              f"{stats.total_model_requests:,}", f"{stats.total_model_turns:,}",
                              f"{stats.total_model_tool_calls:,}", cost(stats, stats.known_cost),
                              cost(stats, stats.daily_cost)))
        combined["tokens"] += stats.total_tokens
        combined["requests"] += stats.total_model_requests
        combined["turns"] += stats.total_model_turns
        combined["tools"] += stats.total_model_tool_calls
        combined["cost"] += stats.known_cost
        combined["daily"] += stats.daily_cost
        for status, count in stats.cost_status_counts.items():
            combined["counts"][status] = combined["counts"].get(status, 0) + count
    combined_cost = _cost_display({"cost": combined["cost"], "cost_status_counts": combined["counts"]})
    combined_daily = _cost_display({"cost": combined["daily"], "cost_status_counts": combined["counts"]})
    overview_rows.append(("COMBINED TOTAL", f"{combined['tokens']:,}", f"{combined['requests']:,}",
                          f"{combined['turns']:,}", f"{combined['tools']:,}", combined_cost, combined_daily))
    leaders = _comparison_leaders(report)
    leading_agent, leading_stats = max(agents, key=lambda item: item[1].total_tokens)
    insights = (
        '<p class="eyebrow">At a glance</p><h1>Usage overview</h1>'
        f'<p>Period: {text(report.period_label)}</p><div class="summary-grid">'
        f'<div class="insight"><span>Most usage</span><strong>{text(AGENT_NAMES.get(leading_agent, leading_agent))}</strong><span>{leading_stats.total_tokens:,} tokens</span></div>'
        f'<div class="insight"><span>Known cost</span><strong>{text(combined_cost)}</strong><span>Across all analyzed harnesses</span></div>'
        f'<div class="insight"><span>Harnesses analyzed</span><strong>{len(agents)}</strong><span>Local sources only</span></div></div>'
    )
    leader_tables = "<section><h2>TOKEN LEADERS</h2>" + table(
        ("Agent", "Provider", "Model", "Tokens"),
        [(AGENT_NAMES.get(agent, agent), leader["provider"], leader["model"], f"{leader['tokens']:,}") for agent, leader in leaders["agents"].items()],
    ) + "<h3>COMBINED TOP 3</h3>" + table(
        ("Rank", "Provider", "Model", "Tokens"),
        [(index, leader["provider"], leader["model"], f"{leader['tokens']:,}") for index, leader in enumerate(leaders["combined"], 1)],
    ) + "</section>"
    pages = {"index.html": page("Eurysx report", "index.html", insights + leader_tables + "<section><h2>COMPARISON SUMMARY</h2>" + table(("Agent", "Total tokens", "Requests", "Turns", "Tool calls", "Known cost", "Daily known"), overview_rows) + "</section>")}

    for agent, stats in agents:
        display = report.agent_displays.get(agent)
        period = f"{display.start_date} to {display.end_date} ({display.label})" if display else report.period_label
        total = table(("Metric", "Value"), (("Model requests", f"{stats.total_model_requests:,}"), ("Model turns", f"{stats.total_model_turns:,}"), ("Model tool calls", f"{stats.total_model_tool_calls:,}"), ("Input tokens", f"{stats.total_input_tokens:,}"), ("Output tokens", f"{stats.total_output_tokens:,}"), ("Cache read tokens", f"{stats.total_cache_read_tokens:,}"), ("Cache creation tokens", f"{stats.total_cache_write_tokens:,}"), ("Grand total tokens", f"{stats.total_tokens:,}"), ("Known cost", cost(stats, stats.known_cost)), ("Actual recorded cost", f"${stats.actual_cost:,.6f}" if stats.cost_status_counts.get("recorded") else "N/A"), ("API-equivalent estimate", f"${stats.api_equivalent_estimate:,.6f}" if stats.estimate_status_counts.get("estimated") else "N/A"), ("Cost context", cost_context(stats))))
        sections = [f"<h1>{text(AGENT_NAMES.get(agent, agent))}</h1><p>Analysis period: {text(period)}</p>", section("TOTAL USAGE", total, open=True)]
        for warning in stats.scope_warnings:
            sections.append(f"<p>Warning: {text(warning)}</p>")
        total_days = (display.end_date - display.start_date).days + 1 if display and display.start_date else 0
        active_days = len(stats.daily_activity)
        daily_tokens = stats.total_tokens / total_days if total_days else 0
        sections.extend((
            section("COST PROJECTIONS", table(("Period", "Known cost"), (("Daily", cost(stats, stats.daily_cost)), ("Weekly", cost(stats, stats.weekly_cost)), ("Monthly", cost(stats, stats.monthly_cost)), ("Quarterly", cost(stats, stats.quarterly_cost)), ("Yearly", cost(stats, stats.yearly_cost))))),
            section("TOKEN VOLUME", table(("Period", "Tokens"), (("Daily", f"{daily_tokens:,.0f}"), ("Active-day average", f"{stats.total_tokens / active_days:,.0f}" if active_days else "N/A"), ("Weekly", f"{daily_tokens * 7:,.0f}"), ("Monthly", f"{daily_tokens * 30:,.0f}"), ("Quarterly", f"{daily_tokens * 90:,.0f}"), ("Yearly", f"{daily_tokens * 365:,.0f}")))),
            section("MODEL ACTIVITY VOLUME", table(("Period", "Requests", "Turns", "Tool calls"), [(label, f"{stats.total_model_requests / total_days * multiplier:,.0f}" if total_days else "N/A", f"{stats.total_model_turns / total_days * multiplier:,.0f}" if total_days else "N/A", f"{stats.total_model_tool_calls / total_days * multiplier:,.0f}" if total_days else "N/A") for label, multiplier in (("Daily", 1), ("Weekly", 7), ("Monthly", 30), ("Quarterly", 90), ("Yearly", 365))])),
            section("COST ANALYSIS", table(("Metric", "Value"), (("Known cost", cost(stats, stats.known_cost)), ("Unknown-cost entries", f"{stats.unknown_cost_count:,}"), ("Unknown metered-cost tokens", f"{stats.unknown_cost_tokens:,}"), ("Metered token coverage", f"{stats.priced_token_coverage:.1%}" if stats.priced_token_coverage is not None else "N/A"), *[(f"{mode.title()} tokens", f"{tokens:,}") for mode, tokens in sorted(stats.non_metered_tokens.items())]))),
        ))
        for title, label, data, noun in (("BREAKDOWN BY MODEL", "Model", stats.model_breakdown, "models"), ("BREAKDOWN BY PROJECT", "Project", stats.project_breakdown, "projects"), ("BREAKDOWN BY SESSION", "Session", stats.session_breakdown, "sessions")):
            count = len(data)
            sections.append(section(title, table((label, "Tokens", "Known cost", "Actual recorded", "API-equivalent estimate", "Cost status"), [(key, f"{item['input'] + item['output'] + item['cache_read'] + item['cache_write']:,}", _cost_display(item), _lane_display(item, "actual_cost"), _lane_display(item, "api_equivalent_estimate"), _cost_status(item)) for key, item in sorted(data.items())], sortable=True), meta=f"{count} {noun[:-1] if count == 1 else noun}"))
        days = len(stats.daily_activity)
        sections.append(section("DAILY ACTIVITY", table(("Date", "Tokens", "Known cost", "Actual recorded", "API-equivalent estimate", "Cost status"), [(day, f"{item['tokens']:,}", _cost_display(item), _lane_display(item, "actual_cost"), _lane_display(item, "api_equivalent_estimate"), _cost_status(item)) for day, item in sorted(stats.daily_activity.items())], sortable=True), meta=f"{days} {'day' if days == 1 else 'days'}"))
        sections.append(section("SUMMARY STATISTICS", table(("Metric", "Value"), (("Sessions", stats.sessions_count), ("Usage entries", stats.usage_entries), ("Unique models", len(stats.unique_models)), ("Cache read ratio", f"{stats.cache_read_ratio:.1%}" if stats.cache_read_ratio is not None else "N/A"), ("Cache efficiency ratio", f"{stats.cache_efficiency_ratio:.1f}:1" if stats.cache_efficiency_ratio is not None else "N/A"), ("Requests per turn", f"{stats.requests_per_turn:.2f}" if stats.requests_per_turn is not None else "N/A"), ("Tool calls per request", f"{stats.tool_calls_per_request:.2f}" if stats.tool_calls_per_request is not None else "N/A"), ("Tool calls per turn", f"{stats.tool_calls_per_turn:.2f}" if stats.tool_calls_per_turn is not None else "N/A")))))
        sections.append(section("PRICING PROVENANCE", table(("Provider", "Observed via", "Model", "Billing mode", "Tokens", "Known cost", "Actual recorded", "API-equivalent estimate", "Cost status"), [(provider_model.split("/", 1)[0], ", ".join(item.get("observed_providers", [])), provider_model.split("/", 1)[1], mode[:-1], f"{item['tokens']:,}", _cost_display(item), _lane_display(item, "actual_cost"), _lane_display(item, "api_equivalent_estimate"), _cost_status(item)) for route, item in sorted(stats.route_breakdown.items()) for provider_model, mode in [route.rsplit(" [", 1)]], sortable=True) + table(("Pricing source", "Kind", "Fetched at"), [(source, stats.pricing_source_kinds.get(source, "unknown"), stats.pricing_fetched_at.get(source, "N/A")) for source in sorted(stats.pricing_sources) or ["No resolved source"]])))
        if stats.unresolved_routes:
            sections.append(section("UNRESOLVED METERED ROUTES", table(("Provider", "Model", "Tokens", "Reason"), [(route["provider"], route["model"], f"{route['tokens']:,}", route["reason"]) for route in stats.unresolved_routes], sortable=True)))
        comparison = report.period_comparison.get(agent)
        if comparison:
            current = comparison["current"]
            previous = comparison["previous"]
            sections.append(section("PERIOD COMPARISON", table(("Metric", "Current", "Previous"), [("Total tokens", f"{current['total_tokens']:,}", f"{previous['total_tokens']:,}"), ("Known cost", _cost_display({"cost": current["known_cost"], "cost_status_counts": current["cost_status_counts"]}), _cost_display({"cost": previous["known_cost"], "cost_status_counts": previous["cost_status_counts"]})), ("Usage entries", f"{current['usage_entries']:,}", f"{previous['usage_entries']:,}"), ("Model requests", f"{current['model_requests']:,}", f"{previous['model_requests']:,}")])))
        pages[f"{agent}.html"] = page(f"Eurysx: {agent}", f"{agent}.html", "".join(sections))
    return pages


def build_json_report(report: AnalysisReport) -> Dict:
    """Assemble the JSON `--output` payload from a structured analysis result."""
    return {
        "schema_version": 1,
        "analysis_period": {
            "start": str(report.start_date) if report.start_date else "ALL TIME",
            "end": str(report.end_date),
            "label": report.period_label,
        },
        "agents_analyzed": list(report.agent_stats.keys()),
        "pricing": report.pricing,
        "preferences": report.preferences,
        "agent_stats": {
            agent: _agent_stats_dict(stats)
            for agent, stats in report.agent_stats.items()
        },
        "comparison_summary": {"leaders": _comparison_leaders(report)},
        "period_comparison": {
            agent: {
                **comparison,
                "current": {key: value for key, value in comparison["current"].items()
                            if key != "cost_status_counts"},
                "previous": {key: value for key, value in comparison["previous"].items()
                             if key != "cost_status_counts"},
            }
            for agent, comparison in report.period_comparison.items()
        },
    }
