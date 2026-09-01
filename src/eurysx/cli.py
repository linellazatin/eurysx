"""Command-line orchestration for Eurysx."""

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .analysis import UsageAnalyzer
from .collectors import collect, detect_agents
from .models import AgentStats, UsageEntry
from .paths import get_eurysx_data_dir
from .pricing import PreferencesResolver, PricingResolver, apply_pricing
from .render import Colors, print_agent_header, print_single_agent_report, print_summary_comparison
from .store import UsageStore



def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD") from exc


def _year_month(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise argparse.ArgumentTypeError("must use YYYY-MM")
    try:
        date.fromisoformat(f"{value}-01")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must use a valid YYYY-MM") from exc
    return value


def _year_quarter(value: str) -> str:
    if not re.fullmatch(r"\d{4}-Q[1-4]", value):
        raise argparse.ArgumentTypeError("must use YYYY-QN with N from 1 to 4")
    return value


def _year(value: str) -> int:
    try:
        year = int(value)
        date(year, 1, 1)
        return year
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a valid four-digit year") from exc


def _entries_fingerprint(usages: List[UsageEntry]) -> str:
    metadata = [
        (usage.timestamp, usage.session_id, usage.model_id, usage.provider,
         usage.total_tokens, usage.model_requests, usage.model_turns,
         usage.model_tool_calls, usage.is_metric_only, usage.is_aggregated)
        for usage in usages
    ]
    return hashlib.sha256(json.dumps(metadata, separators=(",", ":")).encode()).hexdigest()


def _usage_from_store(record: Dict[str, Any]) -> UsageEntry:
    recorded = record["recorded_cost_usd"]
    cost = float(recorded) if recorded is not None else 0.0
    return UsageEntry(
        agent=record["agent"], model_id=record["model_id"], timestamp=record["timestamp"],
        input_tokens=record["input_tokens"], output_tokens=record["output_tokens"],
        cache_read_tokens=record["cache_read_tokens"],
        cache_write_tokens=record["cache_write_tokens"],
        total_tokens=record["total_tokens"], cost=cost,
        cost_breakdown={"total": cost} if recorded is not None else {},
        provider=record["provider"], observed_provider=record["observed_provider"],
        cost_status="recorded" if recorded is not None else "unknown",
        session_id=record["session_id"], model_requests=record["model_requests"],
        model_turns=record["model_turns"], model_tool_calls=record["model_tool_calls"],
        is_metric_only=record["event_type"] == "metric",
        is_aggregated=record["event_type"] == "aggregate_usage",
    )


def _promote_command_after_agent(argv):
    """Keep a collect/report token out of a greedy --agent value list."""
    values = list(sys.argv[1:] if argv is None else argv)
    for index, value in enumerate(values):
        if value != "--agent":
            continue
        for candidate_index in range(index + 1, len(values)):
            candidate = values[candidate_index]
            if candidate.startswith("-"):
                break
            if candidate in ("collect", "report"):
                command = values.pop(candidate_index)
                return [command, *values]
    return values


def parse_args(argv=None) -> argparse.Namespace:
    """Parse the default, collect, and stored-report command forms."""
    parser = argparse.ArgumentParser(
        description="Eurysx: local usage intelligence for AI coding agents."
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "command", nargs="?", choices=("collect", "report"),
        help="collect metadata only, or report stored metadata only",
    )
    parser.add_argument(
        "--agent", nargs="+",
        choices=["claude-code", "opencode", "pi", "codex", "all"],
        default=["all"],
        help="Agent(s) to analyze. Use all, or list agents after --agent.",
    )
    period = parser.add_mutually_exclusive_group()
    period.add_argument("-w", "--weeks", type=_positive_int, help="Last N weeks, including today")
    period.add_argument("-d", "--days", type=_positive_int, help="Last N days, including today")
    period.add_argument("--month", type=_year_month, metavar="YYYY-MM", help="Calendar month")
    period.add_argument("--quarter", type=_year_quarter, metavar="YYYY-QN", help="Calendar quarter")
    period.add_argument("--year", type=_year, metavar="YYYY", help="Calendar year")
    period.add_argument("--ytd", action="store_true", help="Year-to-date")
    parser.add_argument("--from", dest="start_date", type=_iso_date, metavar="YYYY-MM-DD",
                        help="Inclusive start date")
    parser.add_argument("--to", dest="end_date", type=_iso_date, metavar="YYYY-MM-DD",
                        help="Inclusive end date; requires --from")
    parser.add_argument("--output", type=str, help="Save results to JSON")
    parser.add_argument("--refresh-pricing", action="store_true",
                        help="Force refresh of enabled remote pricing sources")
    args = parser.parse_args(_promote_command_after_agent(argv))
    if args.end_date and not args.start_date:
        parser.error("--to requires --from")
    if args.start_date and any((args.days, args.weeks, args.month, args.quarter,
                                args.year, args.ytd)):
        parser.error("--from cannot be combined with another period selector")
    if args.start_date and args.end_date and args.start_date > args.end_date:
        parser.error("--from must be on or before --to")
    return args


def get_date_range(args: argparse.Namespace,
                   today: Optional[date] = None) -> Tuple[Optional[date], date, str]:
    """Calculate date range based on flags."""
    today = today or datetime.now().date()
    
    if args.ytd:
        return date(today.year, 1, 1), today, 'YTD'

    if args.start_date:
        end = args.end_date or today
        return args.start_date, end, f'{args.start_date} to {end}'

    if args.month:
        year, month = map(int, args.month.split('-'))
        start = date(year, month, 1)
        end = date(year + 1, 1, 1) - timedelta(days=1) if month == 12 else \
            date(year, month + 1, 1) - timedelta(days=1)
        return start, end, args.month

    if args.quarter:
        year, quarter = args.quarter.split('-Q')
        start_month = (int(quarter) - 1) * 3 + 1
        start = date(int(year), start_month, 1)
        end_month = start_month + 2
        end = date(int(year), end_month + 1, 1) - timedelta(days=1)
        return start, end, args.quarter

    if args.year:
        return date(args.year, 1, 1), date(args.year, 12, 31), str(args.year)
    
    no_flags = args.days is None and args.weeks is None
    
    if no_flags:
        return None, today, 'ALL TIME'
    
    # Calculate based on flags
    if args.weeks:
        days = args.weeks * 7
        label = f"{args.weeks}w"
    else:
        days = args.days
        label = f"{args.days}d"
    
    return today - timedelta(days=days - 1), today, label


def main(argv=None):
    """Run a default collection/report, collection-only, or stored report."""
    args = parse_args(argv)
    if args.output:
        Colors.disable()

    start_date, end_date, period_label = get_date_range(args)
    is_all_time = start_date is None
    resolver = PricingResolver(force_refresh=args.refresh_pricing)
    preferences = PreferencesResolver()
    store = UsageStore(get_eurysx_data_dir() / "eurysx.db")
    for warning in resolver.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    for warning in preferences.warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    if "all" in args.agent:
        if len(args.agent) > 1:
            print("Error: 'all' cannot be combined with other agents.")
            print("Usage: --agent all OR --agent claude-code opencode")
            return
        agents_to_analyze = detect_agents() if args.command != "report" else None
        if not agents_to_analyze and args.command != "report":
            print("No agents detected. Check if any agents are installed.")
            return
    else:
        agents_to_analyze = args.agent

    agent_data = {}
    if args.command == "report":
        for record in store.events(agents_to_analyze):
            usage = _usage_from_store(record)
            agent_data.setdefault(usage.agent, []).append(usage)
        if not agent_data:
            print("No stored usage data found. Run eurysx collect first.")
            return
        print(f"Reporting stored agents: {', '.join(agent_data)}")
        for usages in agent_data.values():
            apply_pricing(usages, resolver, preferences)
    else:
        print(f"Analyzing agents: {', '.join(agents_to_analyze)}")
        for agent in agents_to_analyze:
            print(f"\nCollecting data from {agent}...")
            usages = collect(agent)
            if usages:
                store.replace_source(
                    f"collector:{agent}", agent, _entries_fingerprint(usages), usages
                )
                print(f"  Found {len(usages)} usage entries")
                agent_data[agent] = usages
            else:
                print(f"  No usage data found for {agent}")
        if args.command == "collect":
            return
        for usages in agent_data.values():
            apply_pricing(usages, resolver, preferences)

    all_stats = {}
    for agent, usages in agent_data.items():
        print(f"\nAnalyzing {agent}...")
        display_start_date = start_date
        display_period_label = period_label
        if is_all_time and usages:
            dates = [
                usage_date for usage in usages
                if (usage_date := UsageAnalyzer.extract_date_from_timestamp(usage.timestamp))
            ]
            if dates:
                display_start_date = min(dates)
                display_period_label = f"ALL TIME (data from {display_start_date})"
            else:
                display_start_date = end_date

        stats = UsageAnalyzer.analyze_agent(
            agent, usages, start_date, end_date, period_label,
            include_aggregated=is_all_time,
        )
        all_stats[agent] = stats
        print_single_agent_report(
            agent, usages, stats, display_start_date, end_date, display_period_label
        )

    if len(agent_data) > 1:
        print_summary_comparison(all_stats)

    if args.output:
        output_result = {
            "analysis_period": {
                "start": str(start_date) if start_date else "ALL TIME",
                "end": str(end_date),
                "label": period_label,
            },
            "agents_analyzed": list(all_stats.keys()),
            "pricing": {
                "config_file": str(resolver.config_path),
                "sources": resolver.fetched_at,
                "warnings": resolver.warnings,
            },
            "preferences": {
                "config_file": str(preferences.config_path),
                "warnings": preferences.warnings,
            },
            "agent_stats": {},
        }
        for agent, stats in all_stats.items():
            output_result["agent_stats"][agent] = {
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
        with open(args.output, "w") as output_file:
            json.dump(output_result, output_file, indent=2)


if __name__ == "__main__":
    main()
