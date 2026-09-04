"""Command-line orchestration for Eurysx."""

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .analysis import UsageAnalyzer
from .collectors import collect_sources, detect_agents
from .models import AnalysisReport, UsageEntry
from .paths import get_eurysx_data_dir
from .pricing import PreferencesResolver, PricingResolver, apply_pricing
from .render import (
    Colors, build_json_report, print_agent_header,
    print_single_agent_report, print_summary_comparison,
)
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
        session_id=record["session_id"], project_id=record["project_id"],
        model_requests=record["model_requests"],
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


def _refresh_store(store, agents):
    """Collect per raw source, skipping sources whose fingerprint and parser version are unchanged."""
    for agent in agents:
        print(f"\nCollecting data from {agent}...")
        updated = skipped = failed = 0
        for source in collect_sources(agent):
            state = store.source_state(source.key)
            if (
                state
                and state["fingerprint"] == source.fingerprint
                and state["parser_version"] == source.parser_version
            ):
                skipped += 1
                continue
            try:
                entries = source.parse()
            except Exception as error:
                store.record_failure(source.key, error)
                failed += 1
                print(f"  Refresh failed for {source.key}: {error}")
                continue
            store.replace_source(source.key, agent, source.fingerprint, entries,
                                 parser_version=source.parser_version)
            updated += 1
        print(f"  Updated {updated} source(s), {skipped} unchanged, {failed} failed.")


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
        read_agents = None
        agents_to_analyze = None if args.command == "report" else detect_agents()
        if args.command != "report" and not agents_to_analyze:
            print("No agents detected. Check if any agents are installed.")
            return
    else:
        agents_to_analyze = args.agent
        read_agents = args.agent

    agent_data = {}
    if args.command == "report":
        print("Reporting stored agents without collecting.")
    else:
        print(f"Analyzing agents: {', '.join(agents_to_analyze)}")
        _refresh_store(store, agents_to_analyze)
        if args.command == "collect":
            return
    for failure in store.failing_sources():
        print(
            f"Warning: last refresh failed for {failure['source_key']}; "
            f"reporting last good data ({failure['last_error']}).",
            file=sys.stderr,
        )
    for record in store.events(read_agents):
        usage = _usage_from_store(record)
        agent_data.setdefault(usage.agent, []).append(usage)
    if agents_to_analyze:
        agent_data = {a: agent_data[a] for a in agents_to_analyze if a in agent_data}
    if not agent_data:
        print("No stored usage data found. Run eurysx collect first.")
        return
    if args.command == "report":
        print(f"Reporting stored agents: {', '.join(agent_data)}")
    for usages in agent_data.values():
        apply_pricing(usages, resolver, preferences)

    report = AnalysisReport(start_date=start_date, end_date=end_date, period_label=period_label)
    report.pricing = {
        "config_file": str(resolver.config_path),
        "sources": resolver.fetched_at,
        "warnings": resolver.warnings,
    }
    report.preferences = {
        "config_file": str(preferences.config_path),
        "warnings": preferences.warnings,
    }

    for agent, usages in agent_data.items():
        print(f"\nAnalyzing {agent}...")
        stats = UsageAnalyzer.analyze_agent(
            agent, usages, start_date, end_date, period_label,
            include_aggregated=is_all_time,
        )
        report.agent_stats[agent] = stats
        report.agent_displays[agent] = UsageAnalyzer.display_period(
            usages, start_date, end_date, period_label, is_all_time,
        )
        print_single_agent_report(report, agent)

    if len(agent_data) > 1:
        print_summary_comparison(report)

    if args.output:
        with open(args.output, "w") as output_file:
            json.dump(build_json_report(report), output_file, indent=2)


if __name__ == "__main__":
    main()
