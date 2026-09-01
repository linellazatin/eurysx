"""Date and usage aggregation."""

from collections import defaultdict
from datetime import date, datetime
from typing import List, Optional

from .models import AgentStats, UsageEntry


class UsageAnalyzer:
    """Core analysis logic matching pi.py output format."""
    
    @staticmethod
    def extract_date_from_timestamp(timestamp: str) -> Optional[datetime.date]:
        """Extract date from ISO timestamp or epoch timestamp."""
        try:
            if 'T' in timestamp:
                date_str = timestamp.split('T')[0]
                return datetime.strptime(date_str, '%Y-%m-%d').date()
            elif timestamp.isdigit():
                return datetime.fromtimestamp(int(timestamp) / 1000).date()
        except (ValueError, IndexError):
            pass
        return None
    
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
                     include_aggregated: bool = True) -> AgentStats:
        """Analyze usage data for a single agent."""
        filtered_usages = UsageAnalyzer.filter_by_date_range(
            usages, start_date, end_date, include_aggregated
        )
        stats = AgentStats(agent=agent)
        if not include_aggregated and any(usage.is_aggregated for usage in usages):
            stats.scope_warnings.append(
                "Excluded aggregate usage because it cannot be filtered to the selected period."
            )
        if not filtered_usages:
            return stats
        
        model_tokens = defaultdict(lambda: {
            'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'cost': 0.0,
            'model_requests': 0, 'model_turns': 0, 'model_tool_calls': 0
        })
        daily_tokens = defaultdict(lambda: {'tokens': 0, 'cost': 0.0})
        route_tokens = defaultdict(lambda: {
            'tokens': 0, 'cost': 0.0, 'entries': 0,
            'model_requests': 0, 'model_turns': 0, 'model_tool_calls': 0,
        })
        sessions = set()
        
        for usage in filtered_usages:
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
            stats.cost_status_counts[usage.cost_status] = (
                stats.cost_status_counts.get(usage.cost_status, 0) + 1
            )
            if usage.cost_status == "unknown":
                stats.unknown_cost_count += 1
                if billing_mode == "metered":
                    stats.unknown_cost_tokens += usage.total_tokens
            if usage.pricing_source:
                stats.pricing_sources.add(usage.pricing_source)
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
            if usage.cost_status not in ("unknown", "not_applicable"):
                model_tokens[usage.model_id]['cost'] += usage.cost
                route_data['cost'] += usage.cost
            
            stats.total_input_tokens += usage.input_tokens
            stats.total_output_tokens += usage.output_tokens
            stats.total_cache_read_tokens += usage.cache_read_tokens
            stats.total_cache_write_tokens += usage.cache_write_tokens
            stats.total_tokens += usage.total_tokens
            if usage.cost_status not in ("unknown", "not_applicable"):
                stats.known_cost += usage.cost
                stats.total_cost += usage.cost
            
            date = UsageAnalyzer.extract_date_from_timestamp(usage.timestamp)
            if date:
                date_str = date.strftime('%Y-%m-%d')
                daily_tokens[date_str]['tokens'] += usage.total_tokens
                daily_tokens[date_str]['cost'] += usage.cost
        
        stats.sessions_count = len(sessions)
        stats.model_breakdown = dict(model_tokens)
        stats.route_breakdown = dict(route_tokens)
        stats.daily_activity = dict(daily_tokens)
        if stats.metered_tokens:
            stats.priced_token_coverage = (
                (stats.metered_tokens - stats.unknown_cost_tokens) / stats.metered_tokens
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
