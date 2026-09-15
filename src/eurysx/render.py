"""Report rendering: terminal presentation and the JSON export payload."""

import csv
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
    known = counts.get("recorded", 0) + counts.get("configured", 0)
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


def _print_table(headers, rows):
    rows = [[str(value) for value in row] for row in rows]
    widths = [max(len(header), *(len(row[index]) for row in rows)) for index, header in enumerate(headers)]
    print("  ".join(f"{header:<{width}}" for header, width in zip(headers, widths)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(f"{value:<{width}}" for value, width in zip(row, widths)))


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
        (attribution_noun.title(), "Tokens", "Known cost", "Cost status", "Requests", "Turns", "Tool calls"),
        [
            (
                key,
                f"{data['input'] + data['output'] + data['cache_read'] + data['cache_write']:,}",
                _cost_display(data), _cost_status(data),
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
    
    print(f"\nModel requests:        {stats.total_model_requests:>15,}")
    print(f"Model turns:           {stats.total_model_turns:>15,}")
    print(f"Model tool calls:      {stats.total_model_tool_calls:>15,}")
    print(f"\nInput tokens:          {stats.total_input_tokens:>15,}")
    print(f"Output tokens:         {stats.total_output_tokens:>15,}")
    print(f"Cache read tokens:     {stats.total_cache_read_tokens:>15,}")
    print(f"Cache creation tokens: {stats.total_cache_write_tokens:>15,}")
    print(f"GRAND TOTAL TOKENS:    {stats.total_tokens:>15,}")
    print(f"\nKNOWN COST:            ${stats.known_cost:>14,.6f}")
    
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
        ("Model", "Tokens", "Known cost", "Cost status", "Requests", "Turns", "Tool calls"),
        [
            (
                model_id,
                f"{model_data['input'] + model_data['output'] + model_data['cache_read'] + model_data['cache_write']:,}",
                _cost_display(model_data), _cost_status(model_data),
                f"{model_data['model_requests']:,}", f"{model_data['model_turns']:,}",
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

    print(f"\nDaily (across all {total_days} days):        ${stats.daily_cost:>14,.6f}")
    print(f"Daily (active days only, {days_active} days): {active_cost:>15}")
    print(f"Weekly (across {total_days/7:.1f} weeks):            ${stats.weekly_cost:>14,.6f}")
    print(f"Monthly (30-day avg, {total_days/30:.1f} months):      ${stats.monthly_cost:>14,.6f}")
    print(f"Quarterly (90-day avg, {total_days/90:.1f} quarters): ${stats.quarterly_cost:>14,.6f}")
    print(f"Yearly (365-day avg, {total_days/365:.2f} years):      ${stats.yearly_cost:>14,.6f}")
    
    # ===== TOKEN VOLUME PER TIME PERIOD =====
    print(f"\n{color}{'=' * 80}{Colors.reset}")
    print(f"{color}TOKEN VOLUME PER TIME PERIOD{Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")
    
    token_daily_avg = stats.total_tokens / total_days if total_days > 0 else 0
    token_weekly = token_daily_avg * 7
    token_monthly = token_daily_avg * 30
    token_quarterly = token_daily_avg * 90
    token_yearly = token_daily_avg * 365
    
    print(f"\nDaily (across all days):     {token_daily_avg:>15,.0f} tokens")
    print(f"Daily (active days only):    {active_tokens:>15} tokens")
    print(f"Weekly:                      {token_weekly:>15,.0f} tokens")
    print(f"Monthly (30-day avg):        {token_monthly:>15,.0f} tokens")
    print(f"Quarterly (90-day avg):      {token_quarterly:>15,.0f} tokens")
    print(f"Yearly (365-day avg):        {token_yearly:>15,.0f} tokens")

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
    
    print(f"\nTotal sessions: {stats.sessions_count}"
          + (" attributed (+ unattributed rows)" if "unknown" in stats.session_breakdown else ""))
    print(f"Total messages (usage entries): {stats.usage_entries}")
    print(f"Unique models used: {len(stats.unique_models)}")
    
    # ===== CACHE EFFECTIVENESS =====
    print(f"\n{color}{'=' * 80}{Colors.reset}")
    print(f"{color}CACHE EFFECTIVENESS{Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")
    
    total_cache_tokens = stats.total_cache_read_tokens + stats.total_cache_write_tokens
    if stats.cache_read_ratio is not None:
        print(f"Cache read ratio: {stats.cache_read_ratio:.1%} ({stats.total_cache_read_tokens:,} / {total_cache_tokens:,})")

    if stats.cache_efficiency_ratio is not None:
        print(f"Cache efficiency ratio: {stats.cache_efficiency_ratio:.1f}:1")
    
    # ===== COST ANALYSIS =====
    print(f"\n{color}{'=' * 80}{Colors.reset}")
    print(f"{color}COST ANALYSIS{Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")
    
    actual_cost = stats.known_cost
    
    print(f"\nKnown reported or estimated cost:     ${actual_cost:>14,.6f}")
    print(f"Unknown-cost entries:                  {stats.unknown_cost_count:>15,}")
    print(f"Unknown metered-cost tokens:          {stats.unknown_cost_tokens:>15,}")
    coverage = f"{stats.priced_token_coverage:.1%}" if stats.priced_token_coverage is not None else "N/A"
    print(f"Metered token coverage:                {coverage:>14}")
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
    for billing_mode, tokens in sorted(stats.non_metered_tokens.items()):
        print(f"{billing_mode.title()} tokens:                  {tokens:>15,}")

    print("\nRoute breakdown:")
    route_rows = []
    for route, route_data in sorted(stats.route_breakdown.items()):
        provider_model, mode = route.rsplit(" [", 1)
        provider, model = provider_model.split("/", 1)
        route_rows.append((
            provider, model, mode[:-1], f"{route_data['tokens']:,}",
            _cost_display(route_data), _cost_status(route_data), f"{route_data['entries']:,}",
        ))
    _print_table(
        ("Provider", "Model", "Billing mode", "Tokens", "Known cost", "Cost status", "Entries"),
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
    writer.writerow(("agent", "provider", "model", "billing_mode", "tokens", "known_cost_usd", "entries", "model_requests", "model_turns", "model_tool_calls", "cost_status"))
    for agent, stats in sorted(report.agent_stats.items()):
        for route, data in sorted(stats.route_breakdown.items()):
            provider_model, mode = route.rsplit(" [", 1)
            provider, model = provider_model.split("/", 1)
            status = _cost_status(data)
            cost = data["cost"] if status in ("known", "partial") else "N/A"
            writer.writerow((agent, provider, model, mode[:-1], data["tokens"], cost, data["entries"], data["model_requests"], data["model_turns"], data["model_tool_calls"], status))
    return output.getvalue()


def build_markdown_report(report: AnalysisReport) -> str:
    lines = ["# Eurysx report", "", f"Period: {report.period_label}"]
    for agent, stats in sorted(report.agent_stats.items()):
        lines += ["", f"## {agent}", "", f"Tokens: {stats.total_tokens:,}", f"Known cost: ${stats.known_cost:.6f}", "", "| Provider | Model | Billing mode | Tokens | Known cost | Cost status |", "| --- | --- | --- | ---: | ---: | --- |"]
        for route, data in sorted(stats.route_breakdown.items()):
            provider_model, mode = route.rsplit(" [", 1)
            provider, model = provider_model.split("/", 1)
            lines.append(f"| {provider} | {model} | {mode[:-1]} | {data['tokens']:,} | {_cost_display(data)} | {_cost_status(data)} |")
    return "\n".join(lines) + "\n"


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
        "period_comparison": report.period_comparison,
    }
