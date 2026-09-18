"""Date and usage aggregation."""

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from .models import AggregateImportSummary, AgentDisplay, AgentStats, UsageEntry


def _comparison_fields(stats: AgentStats) -> Dict[str, Any]:
    """Compact per-period figures for the period comparison block."""
    return {
        "total_tokens": stats.total_tokens,
        "known_cost": stats.known_cost,
        "cost_status_counts": stats.cost_status_counts,
        "usage_entries": stats.usage_entries,
        "model_requests": stats.total_model_requests,
    }


class UsageAnalyzer:
    """Core analysis logic matching pi.py output format."""
    
    @staticmethod
    def extract_date_from_timestamp(timestamp: str) -> Optional[datetime.date]:
        """Extract date from an ISO timestamp, a date-only stamp (the
        claude-code stats cache records `lastComputedDate` as `YYYY-MM-DD`),
        or an epoch-millis timestamp."""
        try:
            if 'T' in timestamp:
                date_str = timestamp.split('T')[0]
                return datetime.strptime(date_str, '%Y-%m-%d').date()
            elif timestamp.isdigit():
                return datetime.fromtimestamp(int(timestamp) / 1000).date()
            return datetime.strptime(timestamp, '%Y-%m-%d').date()
        except (ValueError, TypeError, IndexError):
            pass
        return None

    @staticmethod
    def display_period(
        usages: List[UsageEntry],
        start_date: Optional[date],
        end_date: date,
        period_label: str,
        is_all_time: bool,
    ) -> AgentDisplay:
        """Per-agent period shown in the terminal report; all-time mode pins it to first usage."""
        if not is_all_time or not usages:
            return AgentDisplay(start_date, end_date, period_label)
        dates = [
            usage_date for usage in usages
            if (usage_date := UsageAnalyzer.extract_date_from_timestamp(usage.timestamp))
        ]
        if dates:
            first = min(dates)
            return AgentDisplay(first, end_date, f"ALL TIME (data from {first})")
        return AgentDisplay(end_date, end_date, period_label)

    @staticmethod
    def filter_by_date_range(usages: List[UsageEntry], start_date: Optional[date],
                             end_date: date, include_aggregated: bool = True) -> List[UsageEntry]:
        """Filter usage entries by date range, retaining aggregates only for all time."""
        filtered = []
        for usage in usages:
            if getattr(usage, 'is_aggregated', False):
                if include_aggregated:
                    filtered.append(usage)
                continue
            if start_date is None:
                filtered.append(usage)
                continue
            date = UsageAnalyzer.extract_date_from_timestamp(usage.timestamp)
            if date and start_date <= date <= end_date:
                filtered.append(usage)
        return filtered
    
    @staticmethod
    def analyze_agent(agent: str, usages: List[UsageEntry], start_date: Optional[date],
                     end_date: date, period_label: str,
                     include_aggregated: bool = True,
                     aggregates_present: bool = False,
                     billing_modes=None) -> AgentStats:
        """Analyze usage data for a single agent."""
        filtered_usages = UsageAnalyzer.filter_by_date_range(
            usages, start_date, end_date, include_aggregated
        )
        stats = AgentStats(agent=agent)
        if not include_aggregated and aggregates_present:
            stats.scope_warnings.append(
                "Excluded aggregate usage because it cannot be filtered to the selected period."
            )
        if not filtered_usages:
            return stats
        
        model_tokens = defaultdict(lambda: {
            'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'cost': 0.0,
            'actual_cost': None, 'api_equivalent_estimate': None, 'estimate_status_counts': {},
            'cost_status_counts': {}, 'model_requests': 0, 'model_turns': 0, 'model_tool_calls': 0
        })
        project_tokens = defaultdict(lambda: {
            'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'cost': 0.0,
            'actual_cost': None, 'api_equivalent_estimate': None, 'estimate_status_counts': {},
            'cost_status_counts': {}, 'model_requests': 0, 'model_turns': 0, 'model_tool_calls': 0
        })
        session_tokens = defaultdict(lambda: {
            'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'cost': 0.0,
            'actual_cost': None, 'api_equivalent_estimate': None, 'estimate_status_counts': {},
            'cost_status_counts': {}, 'model_requests': 0, 'model_turns': 0, 'model_tool_calls': 0
        })
        daily_tokens = defaultdict(lambda: {'tokens': 0, 'cost': 0.0, 'actual_cost': None, 'api_equivalent_estimate': None, 'estimate_status_counts': {}, 'cost_status_counts': {}})
        route_tokens = defaultdict(lambda: {
            'tokens': 0, 'cost': 0.0, 'actual_cost': None, 'api_equivalent_estimate': None,
            'estimate_status_counts': {}, 'entries': 0, 'cost_status_counts': {}, 'observed_providers': [],
            'model_requests': 0, 'model_turns': 0, 'model_tool_calls': 0,
        })
        sessions = set()
        
        for usage in filtered_usages:
            if billing_modes is not None and usage.billing_mode not in billing_modes:
                continue
            billing_mode = usage.billing_mode
            route_key = f"{usage.provider or 'unknown'}/{usage.model_id} [{billing_mode}]"
            route_data = route_tokens[route_key]
            route_data['model_requests'] += usage.model_requests
            route_data['model_turns'] += usage.model_turns
            route_data['model_tool_calls'] += usage.model_tool_calls
            stats.total_model_requests += usage.model_requests
            stats.total_model_turns += usage.model_turns
            stats.total_model_tool_calls += usage.model_tool_calls
            model_tokens[usage.model_id]['model_requests'] += usage.model_requests
            model_tokens[usage.model_id]['model_turns'] += usage.model_turns
            model_tokens[usage.model_id]['model_tool_calls'] += usage.model_tool_calls
            project_tokens[usage.project_id or 'unknown']['model_requests'] += usage.model_requests
            project_tokens[usage.project_id or 'unknown']['model_turns'] += usage.model_turns
            project_tokens[usage.project_id or 'unknown']['model_tool_calls'] += usage.model_tool_calls
            session_tokens[usage.session_id or 'unknown']['model_requests'] += usage.model_requests
            session_tokens[usage.session_id or 'unknown']['model_turns'] += usage.model_turns
            session_tokens[usage.session_id or 'unknown']['model_tool_calls'] += usage.model_tool_calls
            if usage.is_metric_only:
                continue
            stats.billing_mode_tokens[billing_mode] = (
                stats.billing_mode_tokens.get(billing_mode, 0) + usage.total_tokens
            )
            if billing_mode == 'metered':
                stats.metered_tokens += usage.total_tokens
            elif billing_mode != 'unknown':
                stats.non_metered_tokens[billing_mode] = (
                    stats.non_metered_tokens.get(billing_mode, 0) + usage.total_tokens
                )
            route_data['tokens'] += usage.total_tokens
            route_data['entries'] += 1
            observed = usage.observed_provider or usage.provider
            if observed and observed not in route_data['observed_providers']:
                route_data['observed_providers'].append(observed)
            for bucket in (
                route_data, model_tokens[usage.model_id],
                project_tokens[usage.project_id or 'unknown'], session_tokens[usage.session_id or 'unknown'],
            ):
                bucket['cost_status_counts'][usage.cost_status] = (
                    bucket['cost_status_counts'].get(usage.cost_status, 0) + 1
                )
            stats.cost_status_counts[usage.cost_status] = (
                stats.cost_status_counts.get(usage.cost_status, 0) + 1
            )
            stats.estimate_status_counts[usage.estimate_status] = (
                stats.estimate_status_counts.get(usage.estimate_status, 0) + 1
            )
            lane_buckets = (route_data, model_tokens[usage.model_id], project_tokens[usage.project_id or 'unknown'], session_tokens[usage.session_id or 'unknown'])
            for bucket in lane_buckets:
                bucket['estimate_status_counts'][usage.estimate_status] = bucket['estimate_status_counts'].get(usage.estimate_status, 0) + 1
            if usage.actual_cost is not None:
                stats.actual_cost += usage.actual_cost
                for bucket in lane_buckets:
                    bucket['actual_cost'] = (bucket['actual_cost'] or 0.0) + usage.actual_cost
            if usage.api_equivalent_estimate is not None:
                stats.api_equivalent_estimate += usage.api_equivalent_estimate
                for bucket in lane_buckets:
                    bucket['api_equivalent_estimate'] = (bucket['api_equivalent_estimate'] or 0.0) + usage.api_equivalent_estimate
            if usage.estimate_status == "estimated" and usage.estimate_basis:
                stats.estimate_entries.append({"estimate_usd": usage.api_equivalent_estimate, **usage.estimate_basis})
            if usage.cost_status == "unknown":
                stats.unknown_cost_count += 1
                if billing_mode == "metered":
                    stats.unknown_cost_tokens += usage.total_tokens
            if usage.pricing_source:
                stats.pricing_sources.add(usage.pricing_source)
                if usage.pricing_source_kind:
                    stats.pricing_source_kinds[usage.pricing_source] = usage.pricing_source_kind
            if usage.pricing_source and usage.pricing_fetched_at:
                stats.pricing_fetched_at[usage.pricing_source] = usage.pricing_fetched_at
            stats.usage_entries += 1
            stats.unique_models.add(usage.model_id)
            
            session_id = getattr(usage, 'session_id', None)
            if session_id:
                sessions.add(session_id)
            
            model_tokens[usage.model_id]['input'] += usage.input_tokens
            model_tokens[usage.model_id]['output'] += usage.output_tokens
            model_tokens[usage.model_id]['cache_read'] += usage.cache_read_tokens
            model_tokens[usage.model_id]['cache_write'] += usage.cache_write_tokens
            project_tokens[usage.project_id or 'unknown']['input'] += usage.input_tokens
            project_tokens[usage.project_id or 'unknown']['output'] += usage.output_tokens
            project_tokens[usage.project_id or 'unknown']['cache_read'] += usage.cache_read_tokens
            project_tokens[usage.project_id or 'unknown']['cache_write'] += usage.cache_write_tokens
            session_tokens[usage.session_id or 'unknown']['input'] += usage.input_tokens
            session_tokens[usage.session_id or 'unknown']['output'] += usage.output_tokens
            session_tokens[usage.session_id or 'unknown']['cache_read'] += usage.cache_read_tokens
            session_tokens[usage.session_id or 'unknown']['cache_write'] += usage.cache_write_tokens
            if usage.cost_status not in ("unknown", "not_applicable"):
                model_tokens[usage.model_id]['cost'] += usage.cost
                route_data['cost'] += usage.cost
                project_tokens[usage.project_id or 'unknown']['cost'] += usage.cost
                session_tokens[usage.session_id or 'unknown']['cost'] += usage.cost
            
            stats.total_input_tokens += usage.input_tokens
            stats.total_output_tokens += usage.output_tokens
            stats.total_cache_read_tokens += usage.cache_read_tokens
            stats.total_cache_write_tokens += usage.cache_write_tokens
            stats.total_tokens += usage.total_tokens
            if usage.cost_status not in ("unknown", "not_applicable"):
                stats.known_cost += usage.cost
                stats.total_cost += usage.cost
            
            # Aggregate rows cover all recorded history on a single stamp, so
            # they must not land in a per-day trend bucket.
            if not usage.is_aggregated:
                usage_date = UsageAnalyzer.extract_date_from_timestamp(usage.timestamp)
                if usage_date:
                    date_str = usage_date.strftime('%Y-%m-%d')
                    daily_tokens[date_str]['tokens'] += usage.total_tokens
                    daily = daily_tokens[date_str]
                    daily['estimate_status_counts'][usage.estimate_status] = daily['estimate_status_counts'].get(usage.estimate_status, 0) + 1
                    if usage.actual_cost is not None:
                        daily['actual_cost'] = (daily['actual_cost'] or 0.0) + usage.actual_cost
                    if usage.api_equivalent_estimate is not None:
                        daily['api_equivalent_estimate'] = (daily['api_equivalent_estimate'] or 0.0) + usage.api_equivalent_estimate
                    if usage.cost_status not in ("unknown", "not_applicable"):
                        daily_tokens[date_str]['cost'] += usage.cost
                    daily_tokens[date_str]['cost_status_counts'][usage.cost_status] = (
                        daily_tokens[date_str]['cost_status_counts'].get(usage.cost_status, 0) + 1
                    )
        
        stats.sessions_count = len(sessions)
        unresolved = {}
        for usage in filtered_usages:
            if usage.is_metric_only or usage.billing_mode != "metered" or usage.cost_status != "unknown":
                continue
            key = (usage.provider or "unknown", usage.model_id)
            reason = "no enabled source" if not usage.pricing_sources else "no exact provider/model match"
            item = unresolved.setdefault(key, {"provider": key[0], "model": key[1], "tokens": 0, "reason": reason})
            item["tokens"] += usage.total_tokens
        stats.unresolved_routes = list(unresolved.values())
        stats.model_breakdown = dict(model_tokens)
        stats.route_breakdown = dict(route_tokens)
        stats.project_breakdown = dict(project_tokens)
        stats.session_breakdown = dict(session_tokens)
        stats.daily_activity = dict(daily_tokens)
        if stats.metered_tokens:
            stats.priced_token_coverage = (
                (stats.metered_tokens - stats.unknown_cost_tokens) / stats.metered_tokens
            )
        total_cache_tokens = stats.total_cache_read_tokens + stats.total_cache_write_tokens
        if total_cache_tokens > 0:
            stats.cache_read_ratio = stats.total_cache_read_tokens / total_cache_tokens
        if stats.total_cache_read_tokens > 0 and stats.total_cache_write_tokens > 0:
            stats.cache_efficiency_ratio = (
                stats.total_cache_read_tokens / stats.total_cache_write_tokens
            )
        # Denominators of zero stay None (not 0.0): a ranged Claude Code report
        # has no request/turn/tool rows at all, which is unknown, not infinite.
        if stats.total_model_turns:
            stats.requests_per_turn = (
                stats.total_model_requests / stats.total_model_turns
            )
            stats.tool_calls_per_turn = (
                stats.total_model_tool_calls / stats.total_model_turns
            )
        if stats.total_model_requests:
            stats.tool_calls_per_request = (
                stats.total_model_tool_calls / stats.total_model_requests
            )
        
        rate_start_date = start_date
        if rate_start_date is None:
            usage_dates = [
                usage_date for usage in filtered_usages
                if (usage_date := UsageAnalyzer.extract_date_from_timestamp(usage.timestamp))
            ]
            rate_start_date = min(usage_dates) if usage_dates else None
        total_days = (end_date - rate_start_date).days + 1 if rate_start_date else 0
        if total_days > 0:
            stats.daily_cost = stats.total_cost / total_days
            stats.weekly_cost = stats.daily_cost * 7
            stats.monthly_cost = stats.daily_cost * 30
            stats.quarterly_cost = stats.daily_cost * 90
            stats.yearly_cost = stats.daily_cost * 365
        
        return stats

    @staticmethod
    def budget_period(period: str, today: date):
        if period == "week":
            start = today - timedelta(days=today.weekday())
            return start, start + timedelta(days=6)
        if period == "month":
            start = today.replace(day=1)
            return start, (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if period == "quarter":
            month = ((today.month - 1) // 3) * 3 + 1
            start = today.replace(month=month, day=1)
            return start, (start.replace(month=month + 3) if month < 10 else date(today.year + 1, 1, 1)) - timedelta(days=1)
        return today.replace(month=1, day=1), today.replace(month=12, day=31)

    @staticmethod
    def pacing(stats: AgentStats, usages: List[UsageEntry], budget: Dict[str, Any], today: date):
        """Calculate calendar-budget pacing only when metered cost is complete."""
        if any(u.billing_mode == "metered" and u.cost_status == "unknown" for u in usages):
            return {"status": "unavailable", "reason": "unknown metered cost"}
        start, end = UsageAnalyzer.budget_period(budget["period"], today)
        spent = sum(u.cost for u in usages if u.cost_status not in ("unknown", "not_applicable"))
        total_days = (end - start).days + 1
        elapsed_days = (today - start).days + 1
        projected = spent / elapsed_days * total_days
        expected = budget["usd"] * elapsed_days / total_days
        status = "over_budget" if spent > budget["usd"] else "on_track" if spent <= expected else "ahead"
        return {
            "status": status, "budget_usd": budget["usd"], "spent_usd": spent,
            "remaining_usd": max(0.0, budget["usd"] - spent),
            "period_start": start.isoformat(), "period_end": end.isoformat(),
            "elapsed_days": elapsed_days, "remaining_days": total_days - elapsed_days,
            "projected_spend_usd": projected,
        }

    @staticmethod
    def compare_periods(current: AgentStats, previous: AgentStats,
                        current_label: str, previous_label: str) -> Dict[str, Any]:
        """Per-agent previous-vs-current block for the terminal table and JSON."""
        return {
            "current_period": current_label,
            "previous_period": previous_label,
            "current": _comparison_fields(current),
            "previous": _comparison_fields(previous),
        }

    IMPORT_TOKEN_FIELDS = ("input_uncached_tokens", "input_cached_tokens",
                           "cache_creation_tokens", "output_tokens")
    IMPORT_STALE_DAYS = 3

    @staticmethod
    def summarize_imports(rows: List[Dict[str, Any]], *, aggregates_present: bool,
                          today: date, active_filters=()) -> AggregateImportSummary:
        """Roll up stored provider aggregates without reading a single local stat.

        Reported cost and token dimensions stay separate lanes: a usage row has no cost
        and a cost row has no tokens, so neither can be mistaken for the other. Warnings
        are returned, not printed, so presenters and stderr stay separated.
        """
        by_date: Dict[str, Dict[str, Any]] = {}
        by_model: Dict[str, Dict[str, Any]] = {}
        sources: Dict[str, Dict[str, Any]] = {}
        warnings: List[str] = []
        totals = UsageAnalyzer._empty_import_bucket()
        totals["rows_with_reported_cost"] = 0
        for row in rows:
            cost = None if row["cost_usd"] is None else float(row["cost_usd"])
            for bucket, key in ((by_date, row["date"]), (by_model, row["model"] or "unknown")):
                item = bucket.setdefault(key, UsageAnalyzer._empty_import_bucket())
                UsageAnalyzer._add_import_row(item, row, cost)
            UsageAnalyzer._add_import_row(totals, row, cost)
            source = sources.setdefault(row["source_file"], {
                "scope": row["scope_label"], "kind": row["source_kind"], "rows": 0,
                "source_mtime": row["source_mtime"], "ingested_at": row["ingested_at"],
                "newest_complete_date": None,
            })
            source["rows"] += 1
            if row["complete"] and (source["newest_complete_date"] or "") < row["date"]:
                source["newest_complete_date"] = row["date"]
        if any(not row["complete"] for row in rows):
            warnings.append(
                "Warning: newest imported bucket is still in progress; Anthropic data for "
                "today is partial.")
        if rows and aggregates_present:
            warnings.append(
                "Warning: imported aggregates overlap local claude-code aggregate usage; "
                "both lanes are reported separately because an organization aggregate has "
                "no stable join key to local stats.")
        limit = (today - timedelta(days=UsageAnalyzer.IMPORT_STALE_DAYS)).isoformat()
        for path, source in sorted(sources.items()):
            newest = source["newest_complete_date"]
            if newest is not None and newest < limit:
                warnings.append(
                    f"Warning: aggregate import for {source['scope']} is stale; newest "
                    f"complete bucket is {newest} from {path}.")
        spans = {}
        for row in rows:
            dates = spans.setdefault(row["scope_label"], [])
            dates.append(row["date"])
        ranges = {scope: (min(dates), max(dates)) for scope, dates in spans.items()}
        scopes = sorted(ranges)
        for index, first in enumerate(scopes):
            for second in scopes[index + 1:]:
                if ranges[first][0] <= ranges[second][1] and \
                        ranges[second][0] <= ranges[first][1]:
                    warnings.append(
                        f"Warning: imported aggregate sources overlap each other "
                        f"({first}, {second}); shared days may double-count.")
        return AggregateImportSummary(
            rows=list(rows), by_date=by_date, by_model=by_model, totals=totals,
            sources=sources, warnings=warnings, filtered_by=list(active_filters))

    @staticmethod
    def _empty_import_bucket() -> Dict[str, Any]:
        bucket = {"rows": 0, "reported_cost_usd": None, "incomplete_rows": 0}
        for field_name in UsageAnalyzer.IMPORT_TOKEN_FIELDS:
            bucket[field_name] = 0
        return bucket

    @staticmethod
    def _add_import_row(item: Dict[str, Any], row: Dict[str, Any], cost: Optional[float]):
        item["rows"] += 1
        if not row["complete"]:
            item["incomplete_rows"] += 1
        for field_name in UsageAnalyzer.IMPORT_TOKEN_FIELDS:
            if row[field_name] is not None:
                item[field_name] += row[field_name]
        if cost is None:
            return
        item["reported_cost_usd"] = (item["reported_cost_usd"] or 0) + cost
        if "rows_with_reported_cost" in item:
            item["rows_with_reported_cost"] += 1
