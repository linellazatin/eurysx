"""Command-line orchestration for Eurysx."""

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .analysis import UsageAnalyzer
from .collectors import PARSER_VERSIONS, collect_sources, detect_agents
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
            if candidate in ("collect", "report", "doctor"):
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
        "command", nargs="?", choices=("collect", "report", "doctor"),
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
    parser.add_argument("--model", nargs="+",
                        help="Only include usage for these model IDs")
    parser.add_argument("--provider", nargs="+",
                        help="Only include usage for these providers"
                             " ('unknown' matches rows without a recorded provider)")
    parser.add_argument("--billing-mode", nargs="+",
                        choices=["metered", "subscription", "credit", "quota",
                                 "local", "unknown"],
                        help="Only include usage with these billing modes"
                             " (applied after pricing: a recorded cost overrides policy)")
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


def _previous_window(start_date, end_date):
    """Same-length window ending the day before start; None when all-time."""
    if start_date is None:
        return None, None
    previous_end = start_date - timedelta(days=1)
    span = (end_date - start_date).days + 1
    return previous_end - timedelta(days=span - 1), previous_end


def _warn_store_quality(store):
    """Read-only visibility for retained data the current build cannot re-derive.

    Neither warning mutates the store: retention is deliberate (Act II Phase 3),
    and deleting events on an absent harness directory would destroy history.
    `doctor` (Act III Phase 5) owns the per-source detail view.
    """
    stale = {}
    vanished = {}
    for row in store.all_sources():
        agent = row["agent"]
        current = PARSER_VERSIONS.get(agent)
        if current and row["parser_version"] != current:
            stale[(agent, row["parser_version"], current)] = \
                stale.get((agent, row["parser_version"], current), 0) + 1
        path = row["source_key"].split(":", 1)[-1]
        if not Path(path).exists():
            vanished[agent] = vanished.get(agent, 0) + 1
    for (agent, stored, current), count in sorted(stale.items()):
        print(
            f"Warning: {count} stored {agent} source(s) are still on parser "
            f"v{stored} (current v{current}); run 'eurysx collect' so period "
            "filters see them.",
            file=sys.stderr,
        )
    if vanished:
        detail = ", ".join(f"{count} {agent}" for agent, count in sorted(vanished.items()))
        print(
            f"Warning: {detail} stored source(s) no longer exist on disk; their "
            "events are retained as last-good data.",
            file=sys.stderr,
        )


def _print_doctor(store, resolver, preferences):
    """Print read-only local diagnostic state without parsing sources."""
    detected = set(detect_agents())
    print("DOCTOR")
    print("\nDETECTED HARNESSES")
    for agent in PARSER_VERSIONS:
        print(f"{agent}: {'detected' if agent in detected else 'not detected'}")
    descriptors = {}
    for agent in detected:
        try:
            descriptors[agent] = {source.key: source for source in collect_sources(agent)}
        except Exception as error:
            descriptors[agent] = {}
            print(f"Warning: could not inspect {agent} sources: {error}")
    print("\nSTORED SOURCE HEALTH")
    states = store.source_states()
    if not states:
        print("No stored sources.")
    for index, row in enumerate(states, 1):
        source = descriptors.get(row["agent"], {}).get(row["source_key"])
        if source:
            state = "unchanged" if source.fingerprint == row["fingerprint"] else "changed"
        else:
            path = Path(row["source_key"].split(":", 1)[-1])
            state = "unreachable" if not path.exists() else "not collected"
        parser = row["parser_version"]
        current = PARSER_VERSIONS.get(row["agent"])
        drift = f" parser v{parser}" + (f" (current v{current})" if current and parser != current else "")
        error = f"; last error: {row['last_error']}" if row["last_error"] else ""
        print(f"{row['agent']} source {index}: {state}; collected {row['collected_at']};{drift}{error}")
    print("\nPRICING")
    print(f"config: {resolver.config_path}")
    for status in resolver.cache_status():
        print(f"{status['source']}: {status['status']}" +
              (f" ({status['fetched_at']})" if status["fetched_at"] else ""))
    for warning in resolver.warnings:
        print(f"Warning: {warning}")
    print("\nPREFERENCES")
    print(f"config: {preferences.config_path}")
    for warning in preferences.warnings:
        print(f"Warning: {warning}")


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
                store.record_failure(source, agent, error)
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

    if args.command == "doctor":
        store = UsageStore(get_eurysx_data_dir() / "eurysx.db")
        _print_doctor(store, PricingResolver(inspect_only=True), PreferencesResolver())
        return

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
    failures = {}
    for failure in store.failing_sources(read_agents):
        failures[failure["agent"]] = failures.get(failure["agent"], 0) + 1
    for agent, count in sorted(failures.items()):
        print(
            f"Warning: {agent} has last-good data from {count} source(s) "
            "whose latest refresh failed.",
            file=sys.stderr,
        )
    _warn_store_quality(store)
    store_agents = store.distinct_agents(read_agents)
    for record in store.events(read_agents, start_date, end_date,
                               models=args.model, providers=args.provider):
        usage = _usage_from_store(record)
        agent_data.setdefault(usage.agent, []).append(usage)
    prev_start, prev_end = _previous_window(start_date, end_date)
    prev_agent_data = {}
    if prev_start is not None:
        for record in store.events(read_agents, prev_start, prev_end,
                                   models=args.model, providers=args.provider):
            usage = _usage_from_store(record)
            prev_agent_data.setdefault(usage.agent, []).append(usage)
        if agents_to_analyze:
            prev_agent_data = {
                a: prev_agent_data[a] for a in agents_to_analyze if a in prev_agent_data
            }
        prev_label = f"{prev_start} to {prev_end}"
    else:
        prev_label = None
    if agents_to_analyze:
        agent_data = {a: agent_data[a] for a in agents_to_analyze if a in agent_data}
    # Agents present in the store keep their report block even when no row falls
    # inside the period (pre-pushdown these empty blocks came from unfiltered
    # reads); this also keeps aggregate-only agents (claude-code) visible.
    if agents_to_analyze:
        for agent in agents_to_analyze:
            if agent in store_agents:
                agent_data.setdefault(agent, [])
    else:
        for agent in store_agents:
            agent_data.setdefault(agent, [])
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
            aggregates_present=not is_all_time and store.has_aggregate_events([agent]),
            billing_modes=set(args.billing_mode) if args.billing_mode else None,
        )
        report.agent_stats[agent] = stats
        report.agent_displays[agent] = UsageAnalyzer.display_period(
            usages, start_date, end_date, period_label, is_all_time,
        )
        if prev_start is not None:
            prev_usages = prev_agent_data.get(agent, [])
            apply_pricing(prev_usages, resolver, preferences)
            prev_stats = UsageAnalyzer.analyze_agent(
                agent, prev_usages, prev_start, prev_end, prev_label,
                include_aggregated=False,
                aggregates_present=store.has_aggregate_events([agent]),
                billing_modes=set(args.billing_mode) if args.billing_mode else None,
            )
            report.period_comparison[agent] = UsageAnalyzer.compare_periods(
                stats, prev_stats, period_label, prev_label,
            )
        print_single_agent_report(report, agent)

    if len(agent_data) > 1:
        print_summary_comparison(report)

    if args.output:
        with open(args.output, "w") as output_file:
            json.dump(build_json_report(report), output_file, indent=2)


if __name__ == "__main__":
    main()
