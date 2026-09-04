"""Report rendering: terminal presentation and the JSON export payload."""

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
    
    for model_id, model_data in sorted_models:
        total_model_tokens = (model_data['input'] + model_data['output'] + 
                             model_data['cache_read'] + model_data['cache_write'])
        print(f"\n{model_id}:")
        print(f"  Input tokens:         {model_data['input']:>15,}")
        print(f"  Output tokens:        {model_data['output']:>15,}")
        print(f"  Cache read tokens:    {model_data['cache_read']:>15,}")
        print(f"  Cache creation tokens:{model_data['cache_write']:>15,}")
        print(f"  TOTAL TOKENS:         {total_model_tokens:>15,}")
        print(f"  Known cost:           ${model_data['cost']:>14,.6f}")
        print(f"  Model requests:       {model_data['model_requests']:>15,}")
        print(f"  Model turns:          {model_data['model_turns']:>15,}")
        print(f"  Model tool calls:     {model_data['model_tool_calls']:>15,}")
    
    # ===== COST PROJECTIONS PER TIME PERIOD =====
    print(f"\n{color}{'=' * 80}{Colors.reset}")
    print(f"{color}COST PROJECTIONS PER TIME PERIOD{Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")
    
    days_active = len(stats.daily_activity) if stats.daily_activity else 1
    total_days = (display.end_date - display.start_date).days + 1
    
    print(f"\nDaily (across all {total_days} days):        ${stats.daily_cost:>14,.6f}")
    print(f"Daily (active days only, {days_active} days): ${stats.daily_cost if days_active > 0 else 0:>14,.6f}")
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
    print(f"Daily (active days only):    {token_daily_avg:>15,.0f} tokens")
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

    # ===== DAILY ACTIVITY =====
    print(f"\n{color}{'=' * 80}{Colors.reset}")
    print(f"{color}DAILY ACTIVITY{Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")
    print(f"{'Date':<12} {'Tokens':>15} {'Cost':>12}")
    print("-" * 43)
    
    if stats.daily_activity:
        for date in sorted(stats.daily_activity.keys()):
            data = stats.daily_activity[date]
            print(f"{date:<12} {data['tokens']:>15,} ${data['cost']:>11,.6f}")
    else:
        print("No daily activity data available.")
    
    # ===== SUMMARY STATISTICS =====
    print(f"\n{color}{'=' * 80}{Colors.reset}")
    print(f"{color}SUMMARY STATISTICS{Colors.reset}")
    print(f"{color}{'=' * 80}{Colors.reset}")
    
    print(f"\nTotal sessions: {stats.sessions_count}")
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
    for billing_mode, tokens in sorted(stats.non_metered_tokens.items()):
        print(f"{billing_mode.title()} tokens:                  {tokens:>15,}")

    print("\nRoute breakdown:")
    for route, route_data in sorted(stats.route_breakdown.items()):
        print(f"  {route}: {route_data['tokens']:,} tokens, ${route_data['cost']:.6f}")
    
    print("\nKnown cost by top models:")
    top_models = sorted_models[:5] if len(sorted_models) > 5 else sorted_models
    for model_id, model_data in top_models:
        if model_data['cost'] > 0:
            pct = (model_data['cost'] / actual_cost * 100) if actual_cost > 0 else 0
            print(f"  {model_id}: ${model_data['cost']:>12,.2f} ({pct:.1f}%)")
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
    }


def build_json_report(report: AnalysisReport) -> Dict:
    """Assemble the JSON `--output` payload from a structured analysis result."""
    return {
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
    }
