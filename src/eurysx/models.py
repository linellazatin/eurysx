"""Usage data models for Eurysx."""

from dataclasses import dataclass
from typing import Dict, List, Optional, Set


@dataclass
class UsageEntry:
    """Token usage plus optional request, turn, and tool metrics."""
    agent: str
    model_id: str
    timestamp: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int
    cost: float
    cost_breakdown: Dict[str, float]
    provider: Optional[str] = None
    observed_provider: Optional[str] = None
    billing_mode: str = "unknown"
    pricing_provider: Optional[str] = None
    pricing_model: Optional[str] = None
    pricing_sources: List[str] = None
    cost_status: str = "unknown"
    pricing_source: Optional[str] = None
    pricing_fetched_at: Optional[str] = None
    is_aggregated: bool = False
    session_id: Optional[str] = None
    project_id: Optional[str] = None
    model_requests: int = 0
    model_turns: int = 0
    model_tool_calls: int = 0
    is_metric_only: bool = False


@dataclass
class UsageMetrics:
    """Counts are separate because one turn may contain many requests/tools."""
    model_requests: int = 0
    model_turns: int = 0
    model_tool_calls: int = 0


@dataclass
class AgentStats:
    """Aggregated statistics for an agent."""
    agent: str
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    known_cost: float = 0.0
    daily_cost: float = 0.0
    weekly_cost: float = 0.0
    monthly_cost: float = 0.0
    quarterly_cost: float = 0.0
    yearly_cost: float = 0.0
    unique_models: Set[str] = None
    usage_entries: int = 0
    sessions_count: int = 0
    model_breakdown: Dict[str, Dict] = None  # Per-model token/cost breakdown
    daily_activity: Dict[str, Dict] = None  # Per-day token/cost activity
    total_model_requests: int = 0
    total_model_turns: int = 0
    total_model_tool_calls: int = 0
    unknown_cost_count: int = 0
    unknown_cost_tokens: int = 0
    priced_token_coverage: Optional[float] = None
    metered_tokens: int = 0
    non_metered_tokens: Dict[str, int] = None
    billing_mode_tokens: Dict[str, int] = None
    route_breakdown: Dict[str, Dict] = None
    cost_status_counts: Dict[str, int] = None
    pricing_sources: Set[str] = None
    pricing_fetched_at: Dict[str, str] = None
    scope_warnings: List[str] = None
    
    def __post_init__(self):
        if self.unique_models is None:
            self.unique_models = set()
        if self.model_breakdown is None:
            self.model_breakdown = {}
        if self.daily_activity is None:
            self.daily_activity = {}
        if self.cost_status_counts is None:
            self.cost_status_counts = {}
        if self.non_metered_tokens is None:
            self.non_metered_tokens = {}
        if self.billing_mode_tokens is None:
            self.billing_mode_tokens = {}
        if self.route_breakdown is None:
            self.route_breakdown = {}
        if self.pricing_sources is None:
            self.pricing_sources = set()
        if self.pricing_fetched_at is None:
            self.pricing_fetched_at = {}
        if self.scope_warnings is None:
            self.scope_warnings = []



