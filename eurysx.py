#!/usr/bin/env python3
"""Eurysx: local usage intelligence for AI coding agents."""

__version__ = "0.0.1"

import json
import argparse
import sqlite3
import os
import re
import ssl
import subprocess
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Set
from dataclasses import dataclass, asdict
import sys

# ============================================================================
# COLOR SUPPORT (ANSI codes for terminal, disables for file output)
# ============================================================================

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


# ============================================================================
# MODEL PRICING
# ============================================================================

def load_jsonc(path: Path) -> Dict[str, Any]:
    """Load JSON with //, /* */ comments and trailing commas."""
    text = path.read_text()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(^|[^:])//.*", r"\1", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


def _normalise_model(model_id: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(model_id).lower()).strip("-")


def _pricing_values(value: Dict[str, Any]) -> Optional[Dict[str, float]]:
    cost = value.get("cost", value)
    if not isinstance(cost, dict) or "input" not in cost or "output" not in cost:
        return None
    try:
        return {
            "input": float(cost.get("input", 0)),
            "output": float(cost.get("output", 0)),
            "cacheRead": float(cost.get("cacheRead", cost.get("cache_read", 0))),
            "cacheWrite": float(cost.get("cacheWrite", cost.get("cache_write", 0))),
        }
    except (TypeError, ValueError):
        return None


def get_eurysx_dirs(environ: Optional[Dict[str, str]] = None,
                    root: Optional[Path] = None) -> Tuple[Path, Path]:
    """Return checkout-local configuration and cache directories without creating them."""
    environ = os.environ if environ is None else environ
    root = root or Path.cwd()

    config_override = environ.get("EURYSX_CONFIG_DIR")
    cache_override = environ.get("EURYSX_CACHE_DIR")

    return (
        Path(config_override).expanduser() if config_override else root / "config",
        Path(cache_override).expanduser() if cache_override else root / "cache",
    )


class PricingResolver:
    """Resolve configured, cached, and local model pricing without fallback guesses."""

    SCHEMA_VERSION = 2

    def __init__(self, config_path: Optional[Path] = None, cache_dir: Optional[Path] = None,
                 force_refresh: bool = False):
        default_config_dir, default_cache_dir = get_eurysx_dirs()
        self.config_path = config_path or default_config_dir / "pricing.jsonc"
        self.cache_dir = cache_dir or default_cache_dir
        self.force_refresh = force_refresh
        self.models: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.source_priorities: Dict[str, int] = {}
        self.fetched_at: Dict[str, str] = {}
        self.warnings: List[str] = []
        self.config = {}
        if self.config_path.exists():
            try:
                self.config = load_jsonc(self.config_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self.warnings.append(f"pricing configuration ignored: {exc}")
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.warnings.append(f"pricing cache unavailable: {exc}")
        self._load_configured_sources()

    @staticmethod
    def _key(provider: Optional[str], model_id: str) -> str:
        return f"{provider}/{model_id}" if provider else model_id

    def _add(self, provider: Optional[str], model_id: str, pricing: Dict[str, float],
             source: str, fetched_at: Optional[str] = None, priority: int = 100):
        if not model_id:
            return
        keys = [self._key(provider, model_id)]
        normalized_model = _normalise_model(model_id)
        if normalized_model != model_id:
            keys.append(self._key(provider, normalized_model))
        source_models = self.models.setdefault(source, {})
        for key in keys:
            existing = source_models.get(key)
            if existing is None or priority < existing["priority"]:
                source_models[key] = {"pricing": pricing, "source": source,
                                      "fetched_at": fetched_at, "priority": priority}

    def _cache_path(self, source: str) -> Path:
        return self.cache_dir / f"pricing-{source}.json"

    def _cache_fresh(self, path: Path, refresh_days: int) -> bool:
        try:
            data = json.loads(path.read_text())
            fetched = datetime.fromisoformat(data["fetched_at"])
            return datetime.now() - fetched < timedelta(days=refresh_days)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def _load_cache(self, source: str) -> bool:
        try:
            data = json.loads(self._cache_path(source).read_text())
            if data.get("schema_version") != self.SCHEMA_VERSION:
                return False
            fetched = data.get("fetched_at")
            for key, pricing in data.get("models", {}).items():
                if isinstance(pricing, dict):
                    provider, _, model_id = key.partition("/")
                    if not model_id:
                        model_id = provider
                        provider = "amazon-bedrock" if source == "amazon-bedrock" else None
                    self._add(provider if model_id else None, model_id or provider,
                              pricing, source, fetched, data.get("priority", 100))
            self.fetched_at[source] = fetched
            return True
        except (OSError, json.JSONDecodeError, TypeError):
            return False

    def _load_configured_sources(self):
        if not isinstance(self.config, dict):
            self.warnings.append("pricing configuration ignored: top level must be an object")
            return
        sources = self.config.get("sources", {})
        if not isinstance(sources, dict):
            self.warnings.append("pricing configuration ignored: sources must be an object")
            return
        configured = []
        for source, settings in sources.items():
            if isinstance(settings, dict) and settings.get("enabled"):
                priority = self._source_int(source, settings, "priority", 100)
            configured.append((priority, source, settings))
        for priority, source, settings in sorted(configured, key=lambda item: (item[0], item[1])):
            self.source_priorities[source] = priority
            cache_path = self._cache_path(source)
            fresh = cache_path.exists() and not self.force_refresh and self._cache_fresh(
                cache_path, self._source_int(source, settings, "refreshDays", 7))
            if fresh and self._load_cache(source):
                continue
            try:
                models = self._fetch(source, settings)
                normalized_models = dict(models)
                for key, pricing in models.items():
                    provider, separator, model_id = key.partition("/")
                    normalized_key = (
                        f"{provider}/{_normalise_model(model_id)}" if separator
                        else _normalise_model(provider)
                    )
                    normalized_models.setdefault(normalized_key, pricing)
                now = datetime.now().isoformat()
                cache_path.write_text(json.dumps({
                    "schema_version": self.SCHEMA_VERSION,
                    "source": source,
                    "fetched_at": now,
                    "priority": priority,
                    "config": {k: v for k, v in settings.items() if k not in ("token", "apiKey")},
                    "models": normalized_models,
                }, indent=2))
                self._load_cache(source)
            except Exception as exc:
                if self._load_cache(source):
                    self.warnings.append(f"{source} refresh failed; using cached pricing: {exc}")
                else:
                    self.warnings.append(f"{source} unavailable: {exc}")

    def _source_int(self, source: str, settings: Dict[str, Any], key: str, default: int) -> int:
        try:
            return int(settings.get(key, default))
        except (TypeError, ValueError):
            self.warnings.append(f"{source} {key} is invalid; using {default}")
            return default

    def _fetch(self, source: str, settings: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
        if source == "amazon-bedrock":
            command = ["aws", "pricing", "get-products", "--profile",
                       settings.get("profile", ""), "--region", settings.get("region", ""),
                       "--service-code", "AmazonBedrock"]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "aws command failed")
            return self._parse_aws_pricing(json.loads(result.stdout), settings.get("region"))
        if source == "models-dev":
            request = urllib.request.Request(
                settings["url"],
                headers={"User-Agent": "usage-analysis/1.0"},
            )
            with urllib.request.urlopen(
                request, context=self._ssl_context(), timeout=30
            ) as response:
                return self._parse_models_dev(json.loads(response.read()))
        if source == "pi-models-store":
            path = Path.home() / ".pi" / "agent" / "models-store.json"
            return self._parse_pi_models_store(json.loads(path.read_text()))
        raise ValueError(f"unsupported pricing source: {source}")

    @staticmethod
    def _parse_pi_models_store(data: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
        models = {}
        for provider, provider_data in data.items():
            if not isinstance(provider_data, dict):
                continue
            provider_models = provider_data.get("models", [])
            if isinstance(provider_models, dict):
                provider_models = [
                    {"id": model_id, **details}
                    for model_id, details in provider_models.items()
                ]
            for model in provider_models:
                if not isinstance(model, dict):
                    continue
                pricing = _pricing_values(model)
                if pricing and model.get("id"):
                    models[f"{provider}/{model['id']}"] = pricing
        return models

    @staticmethod
    def _ssl_context() -> ssl.SSLContext:
        """Use verified system certificates when the Python framework path is stale."""
        candidates = [
            os.environ.get("SSL_CERT_FILE"),
            "/etc/ssl/cert.pem",
            "/opt/homebrew/etc/openssl@3/cert.pem",
            ssl.get_default_verify_paths().cafile,
        ]
        for cafile in candidates:
            if cafile and Path(cafile).exists():
                try:
                    return ssl.create_default_context(cafile=cafile)
                except ssl.SSLError:
                    continue
        return ssl.create_default_context()

    @staticmethod
    def _parse_aws_pricing(data: Dict[str, Any], region: Optional[str] = None) -> Dict[str, Dict[str, float]]:
        models: Dict[str, Dict[str, float]] = {}
        for item_str in data.get("PriceList", []):
            try:
                item = json.loads(item_str) if isinstance(item_str, str) else item_str
                attrs = item["product"]["attributes"]
                if region and attrs.get("regionCode") not in (None, "", region):
                    continue
                model = attrs.get("model", "")
                inference = attrs.get("inferenceType", "")
                if not model:
                    continue
                token_type = ("input" if "Input" in inference and "Cache" not in inference
                              and "priority" not in inference.lower() else
                              "output" if "Output" in inference else
                              "cacheRead" if "Cache read" in inference or "cacheRead" in inference
                              else "cacheWrite" if "Cache write" in inference or "Cache creation" in inference
                              else None)
                if not token_type:
                    continue
                for term in item.get("terms", {}).get("OnDemand", {}).values():
                    for dimension in term.get("priceDimensions", {}).values():
                        price = float(dimension.get("pricePerUnit", {}).get("USD", 0)) * 1000
                        models.setdefault(model, {"input": 0, "output": 0,
                                                  "cacheRead": 0, "cacheWrite": 0})[token_type] = price
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return models

    @staticmethod
    def _parse_models_dev(data: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
        models = {}
        for provider, provider_data in data.items():
            if not isinstance(provider_data, dict):
                continue
            models_data = provider_data.get("models", {})
            if isinstance(models_data, list):
                models_data = {item.get("id", ""): item for item in models_data
                               if isinstance(item, dict)}
            for model_id, model_data in models_data.items():
                pricing = _pricing_values(model_data) if isinstance(model_data, dict) else None
                if pricing:
                    models[f"{provider}/{model_id}"] = pricing
        return models

    def _model_keys(self, provider: Optional[str], model_id: str) -> List[str]:
        aliases = self.config.get("aliases", {}) if isinstance(self.config, dict) else {}
        provider_aliases = aliases.get(provider, {}) if isinstance(aliases, dict) else {}
        alias = provider_aliases.get(model_id) if isinstance(provider_aliases, dict) else None
        model_ids = [alias, model_id] if isinstance(alias, str) and alias != model_id else [model_id]
        keys = []
        for candidate in model_ids:
            candidates = [candidate]
            normalized = _normalise_model(candidate)
            if normalized != candidate:
                candidates.append(normalized)
            keys.extend(self._key(provider, item) for item in candidates)
        return list(dict.fromkeys(keys))

    def resolve(self, provider: Optional[str], model_id: str,
                sources: Optional[List[str]] = None) -> Dict[str, Any]:
        overrides = self.config.get("overrides", {})
        keys = self._model_keys(provider, model_id)
        for key in keys:
            pricing = _pricing_values(overrides.get(key, {})) if isinstance(overrides, dict) else None
            if pricing:
                return {"pricing": pricing, "status": "configured", "source": "override",
                        "fetched_at": None}
        source_names = sources if sources is not None else sorted(
            self.models, key=lambda name: (self.source_priorities.get(name, 100), name)
        )
        for source in dict.fromkeys(source_names):
            for key in keys:
                entry = self.models.get(source, {}).get(key)
                if entry:
                    return {"pricing": entry["pricing"], "status": "cached",
                            "source": source, "fetched_at": entry["fetched_at"]}
        return {"pricing": None, "status": "unknown", "source": None, "fetched_at": None}


def calculate_cost(input_tokens: int, output_tokens: int, cache_read_tokens: int,
                   cache_write_tokens: int, pricing: Optional[Dict[str, float]]) -> float:
    if not pricing:
        return 0.0
    return sum((
        input_tokens / 1_000_000 * pricing.get("input", 0),
        output_tokens / 1_000_000 * pricing.get("output", 0),
        cache_read_tokens / 1_000_000 * pricing.get("cacheRead", 0),
        cache_write_tokens / 1_000_000 * pricing.get("cacheWrite", 0),
    ))


class PreferencesResolver:
    """Resolve user-owned route and billing policy for supported agents."""

    SUPPORTED_AGENTS = ("claude-code", "codex", "opencode", "pi")
    BILLING_MODES = {"metered", "subscription", "credit", "quota", "local", "unknown"}

    def __init__(self, config_path: Optional[Path] = None):
        default_config_dir, _ = get_eurysx_dirs()
        self.config_path = config_path or default_config_dir / "preferences.jsonc"
        self.warnings: List[str] = []
        self.config: Dict[str, Any] = {}
        self._warned: Set[str] = set()
        if self.config_path.exists():
            try:
                self.config = load_jsonc(self.config_path)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._warn(f"preferences ignored: {exc}")
        self._validate_agents()

    def _warn(self, message: str):
        if message not in self._warned:
            self.warnings.append(message)
            self._warned.add(message)

    def _validate_agents(self):
        if not self.config_path.exists():
            return
        agents = self.config.get("agents") if isinstance(self.config, dict) else None
        if not isinstance(agents, dict):
            self._warn("preferences ignored: agents must be an object")
            return
        for agent in self.SUPPORTED_AGENTS:
            if agent not in agents:
                self._warn(f"preferences missing {agent}; using unknown defaults")

    def apply(self, usage):
        observed_provider = usage.observed_provider or usage.provider
        usage.observed_provider = observed_provider
        policy = self._policy_for(usage.agent, observed_provider)
        usage.provider = policy.get("provider", observed_provider)
        usage.billing_mode = policy["billingMode"]
        usage.pricing_provider = usage.provider
        usage.pricing_model = usage.model_id
        usage.pricing_sources = policy.get("pricingSources", [])

    def _policy_for(self, agent: str, provider: Optional[str]) -> Dict[str, Any]:
        agents = self.config.get("agents", {}) if isinstance(self.config, dict) else {}
        agent_config = agents.get(agent, {}) if isinstance(agents, dict) else {}
        if not isinstance(agent_config, dict):
            self._warn(f"preferences {agent} must be an object; using unknown defaults")
            return {"billingMode": "unknown"}
        policy = {key: agent_config[key] for key in ("provider", "billingMode", "pricing")
                  if key in agent_config}
        providers = agent_config.get("providers", {})
        if providers and not isinstance(providers, dict):
            self._warn(f"preferences {agent}.providers must be an object")
        provider_config = providers.get(provider) if isinstance(providers, dict) and provider else None
        if provider_config is not None and not isinstance(provider_config, dict):
            self._warn(f"preferences {agent}.providers.{provider} must be an object")
        elif isinstance(provider_config, dict):
            policy.update(provider_config)
            policy.setdefault("provider", provider)
        billing_mode = policy.get("billingMode", "unknown")
        if billing_mode not in self.BILLING_MODES:
            self._warn(f"preferences {agent} has invalid billingMode; using unknown")
            billing_mode = "unknown"
        policy["billingMode"] = billing_mode
        pricing = policy.get("pricing", {})
        if pricing and not isinstance(pricing, dict):
            self._warn(f"preferences {agent}.pricing must be an object")
            pricing = {}
        source = pricing.get("source") if isinstance(pricing, dict) else None
        others = pricing.get("otherSources", []) if isinstance(pricing, dict) else []
        if source is not None and not isinstance(source, str):
            self._warn(f"preferences {agent}.pricing.source must be a string")
            source = None
        if not isinstance(others, list) or not all(isinstance(item, str) for item in others):
            self._warn(f"preferences {agent}.pricing.otherSources must be a string list")
            others = []
        policy["pricingSources"] = ([source] if source else []) + others if pricing else []
        return policy


def apply_pricing(usages: List[UsageEntry], resolver: PricingResolver,
                  preferences: Optional[PreferencesResolver] = None):
    """Fill estimated costs while preserving recorded provider costs."""
    for usage in usages:
        if preferences:
            preferences.apply(usage)
        if usage.is_metric_only:
            continue
        if usage.cost_status == "recorded":
            if usage.billing_mode != "metered":
                if preferences:
                    preferences._warn(
                        f"{usage.agent} recorded cost conflicts with {usage.billing_mode}; using metered"
                    )
                usage.billing_mode = "metered"
            usage.pricing_source = usage.pricing_source or "recorded"
            continue
        if usage.billing_mode in {"subscription", "credit", "quota", "local"}:
            usage.cost = 0.0
            usage.cost_breakdown = {}
            usage.cost_status = "not_applicable"
            usage.pricing_source = None
            usage.pricing_fetched_at = None
            continue
        resolved = resolver.resolve(usage.pricing_provider or usage.provider,
                                    usage.pricing_model or usage.model_id,
                                    usage.pricing_sources)
        usage.cost = calculate_cost(
            usage.input_tokens, usage.output_tokens,
            usage.cache_read_tokens, usage.cache_write_tokens,
            resolved["pricing"],
        )
        usage.cost_breakdown = {"total": usage.cost} if resolved["pricing"] else {}
        usage.cost_status = "estimated" if resolved["pricing"] else "unknown"
        usage.pricing_source = resolved["source"]
        usage.pricing_fetched_at = resolved["fetched_at"]


# ============================================================================
# DATA STRUCTURES
# ============================================================================

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


# ============================================================================
# AGENT DATA LOCATIONS
# ============================================================================

class AgentPaths:
    """Configuration for agent data locations."""
    
    @staticmethod
    def claude_code() -> Optional[Path]:
        path = Path.home() / ".claude" / "stats-cache.json"
        return path if path.exists() else None
    
    @staticmethod
    def claude_transcripts() -> List[Path]:
        return list((Path.home() / ".claude").glob("**/*.jsonl"))
    
    @staticmethod
    def opencode() -> Optional[Path]:
        path = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
        return path if path.exists() else None
    
    @staticmethod
    def pi_agent() -> Optional[Path]:
        path = Path.home() / ".pi" / "agent" / "sessions"
        return path if path.exists() and any(path.iterdir()) else None
    
    @staticmethod
    def codex() -> Optional[Path]:
        path = Path.home() / ".codex" / "sessions"
        return path if path.exists() and any(path.glob("*/*/*/rollout-*.jsonl")) else None
    
    @staticmethod
    def detect_agents() -> List[str]:
        """Return list of installed agents based on data files."""
        agents = []
        if AgentPaths.claude_code():
            agents.append('claude-code')
        if AgentPaths.opencode():
            agents.append('opencode')
        if AgentPaths.pi_agent():
            agents.append('pi')
        if AgentPaths.codex():
            agents.append('codex')
        return agents


# ============================================================================
# AGENT-SPECIFIC DATA EXTRACTORS
# ============================================================================

class ClaudeCodeExtractor:
    """Extract usage data from Claude Code stats-cache.json."""
    
    @staticmethod
    def extract_usage() -> List[UsageEntry]:
        """Extract usage data from Claude Code JSON cache."""
        cache_path = AgentPaths.claude_code()
        if not cache_path:
            return []
        
        try:
            with open(cache_path, 'r') as f:
                stats = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error reading Claude Code data: {e}")
            return []
        
        usages = []
        transcript_metrics = ClaudeCodeExtractor.extract_transcript_metrics()
        
        # Extract data from modelUsage
        if 'modelUsage' in stats:
            for model_id, usage_data in stats['modelUsage'].items():
                input_tok = usage_data.get('inputTokens', 0)
                output_tok = usage_data.get('outputTokens', 0)
                cache_read_tok = usage_data.get('cacheReadInputTokens', 0)
                cache_write_tok = usage_data.get('cacheCreationInputTokens', 0)
                total_tok = input_tok + output_tok + cache_read_tok + cache_write_tok
                
                usages.append(UsageEntry(
                    agent='claude-code',
                    model_id=model_id,
                    timestamp=str(stats.get('lastComputedDate', '')),
                    input_tokens=input_tok,
                    output_tokens=output_tok,
                    cache_read_tokens=cache_read_tok,
                    cache_write_tokens=cache_write_tok,
                    total_tokens=total_tok,
                    cost=0.0,
                    cost_breakdown={},
                    provider=None,
                    is_aggregated=True,
                    model_requests=transcript_metrics.get(model_id, UsageMetrics()).model_requests,
                    model_turns=transcript_metrics.get(model_id, UsageMetrics()).model_turns,
                    model_tool_calls=transcript_metrics.get(model_id, UsageMetrics()).model_tool_calls
                ))
        
        return usages

    @staticmethod
    def extract_transcript_metrics() -> Dict[str, UsageMetrics]:
        metrics = defaultdict(UsageMetrics)
        for path in AgentPaths.claude_transcripts():
            try:
                events = []
                with path.open(errors='replace') as transcript:
                    for line in transcript:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                by_id = {event.get('uuid'): event for event in events}
                for event in events:
                    if event.get('type') != 'assistant':
                        continue
                    message = event.get('message', {})
                    model = message.get('model')
                    if not model or not message.get('usage'):
                        continue
                    item = metrics[model]
                    item.model_requests += 1
                    if by_id.get(event.get('parentUuid'), {}).get('type') == 'user':
                        item.model_turns += 1
                    content = message.get('content', [])
                    if isinstance(content, list):
                        item.model_tool_calls += sum(
                            1 for block in content
                            if isinstance(block, dict) and block.get('type') == 'tool_use'
                        )
            except OSError:
                continue
        return dict(metrics)


class OpenCodeExtractor:
    """Extract usage and event metrics from OpenCode SQLite storage."""

    @staticmethod
    def extract_usage() -> List[UsageEntry]:
        db_path = AgentPaths.opencode()
        if not db_path:
            return []
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            usages = []
            for session_id, timestamp_ms, model_json, input_tokens, output_tokens, cache_read, cache_write, cost in cursor.execute("""
                SELECT id, time_created, model, tokens_input, tokens_output,
                       tokens_cache_read, tokens_cache_write, cost
                FROM session
                WHERE tokens_input > 0 OR tokens_output > 0 OR tokens_cache_read > 0 OR tokens_cache_write > 0 OR cost > 0
                ORDER BY time_created
            """):
                try:
                    model_data = json.loads(model_json or '{}')
                    model_id = model_data.get('id', 'unknown')
                    provider = model_data.get('providerID') or model_data.get('provider')
                except json.JSONDecodeError:
                    model_id = str(model_json or 'unknown')
                    provider = None
                total_tokens = sum((input_tokens or 0, output_tokens or 0, cache_read or 0, cache_write or 0))
                usages.append(UsageEntry(
                    agent='opencode', model_id=model_id, timestamp=str(timestamp_ms),
                    input_tokens=input_tokens or 0, output_tokens=output_tokens or 0,
                    cache_read_tokens=cache_read or 0, cache_write_tokens=cache_write or 0,
                    total_tokens=total_tokens, cost=cost or 0.0,
                    cost_breakdown={'total': cost or 0.0} if cost is not None else {},
                    provider=provider, observed_provider=provider,
                    cost_status='recorded' if cost is not None else 'unknown',
                    session_id=session_id
                ))

            messages = cursor.execute("""
                SELECT id, session_id, time_created, data FROM message
            """).fetchall()
            message_roles = {}
            parsed_messages = []
            for message_id, session_id, timestamp_ms, message_json in messages:
                try:
                    message = json.loads(message_json or '{}')
                except json.JSONDecodeError:
                    continue
                message_roles[message_id] = message.get('role')
                parsed_messages.append((session_id, timestamp_ms, message))
            for session_id, timestamp_ms, message in parsed_messages:
                if message.get('role') != 'assistant':
                    continue
                usages.append(UsageEntry(
                    agent='opencode', model_id=message.get('modelID', 'unknown'), timestamp=str(timestamp_ms),
                    input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
                    total_tokens=0, cost=0.0, cost_breakdown={}, session_id=session_id,
                    provider=message.get('providerID') or message.get('provider'),
                    observed_provider=message.get('providerID') or message.get('provider'),
                    model_requests=1,
                    model_turns=1 if message_roles.get(message.get('parentID')) == 'user' else 0,
                    is_metric_only=True
                ))

            tools = cursor.execute("""
                SELECT p.session_id, p.time_created, p.data, s.model FROM part p
                JOIN session s ON s.id = p.session_id
                WHERE json_extract(p.data, '$.type') = 'tool'
            """).fetchall()
            for session_id, timestamp_ms, part_json, model_json in tools:
                try:
                    model_data = json.loads(model_json or '{}')
                    model_id = model_data.get('id', 'unknown')
                    provider = model_data.get('providerID') or model_data.get('provider')
                except json.JSONDecodeError:
                    model_id = 'unknown'
                    provider = None
                usages.append(UsageEntry(
                    agent='opencode', model_id=model_id, timestamp=str(timestamp_ms),
                    input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
                    total_tokens=0, cost=0.0, cost_breakdown={}, session_id=session_id,
                    provider=provider, observed_provider=provider,
                    model_tool_calls=1, is_metric_only=True
                ))
            conn.close()
            return usages
        except sqlite3.Error as e:
            print(f"Error reading OpenCode database: {e}")
            return []


class PiAgentExtractor:
    """Extract usage data from Pi agent session files."""
    
    @staticmethod
    def get_all_session_files() -> List[Path]:
        """Get all session JSONL files from pi agent sessions directory."""
        base_dir = Path.home() / ".pi" / "agent" / "sessions"
        session_files = []
        
        if not base_dir.exists():
            return session_files
        
        for session_dir in base_dir.iterdir():
            if session_dir.is_dir():
                for file in session_dir.glob("*.jsonl"):
                    session_files.append(file)
        
        return session_files
    
    @staticmethod
    def parse_session_line(line: str) -> Optional[Dict]:
        """Parse a single session line and return event dict."""
        try:
            return json.loads(line.strip())
        except json.JSONDecodeError:
            return None
    
    @staticmethod
    def extract_usage_from_session(session_file: Path) -> List[UsageEntry]:
        """Extract usage data from a session file."""
        usages = []
        
        try:
            with open(session_file, 'r') as f:
                events = []
                for line in f:
                    event = PiAgentExtractor.parse_session_line(line)
                    if event:
                        events.append(event)
            by_id = {event.get('id'): event for event in events}
            for event in events:
                if event.get('type') != 'message':
                    continue
                msg = event.get('message', {})
                usage = msg.get('usage')
                timestamp = event.get('timestamp', '')
                session_id = event.get('sessionId')
                if usage and usage.get('totalTokens', 0) > 0:
                    model_id = msg.get('model', '')
                    provider = msg.get('provider') or msg.get('providerID')
                    recorded_cost = usage.get('cost', {}).get('total') if isinstance(usage.get('cost'), dict) else None
                    parent = by_id.get(event.get('parentId'), {}).get('message', {})
                    usages.append(UsageEntry(
                        agent='pi', model_id=model_id, timestamp=timestamp,
                        input_tokens=usage.get('input', 0), output_tokens=usage.get('output', 0),
                        cache_read_tokens=usage.get('cacheRead', 0), cache_write_tokens=usage.get('cacheWrite', 0),
                        total_tokens=usage.get('totalTokens', 0), cost=recorded_cost or 0.0,
                        cost_breakdown=usage.get('cost', {}) if recorded_cost is not None else {},
                        provider=provider, cost_status='recorded' if recorded_cost is not None else 'unknown',
                        observed_provider=provider,
                        session_id=session_id,
                        model_requests=1, model_turns=1 if parent.get('role') == 'user' else 0
                    ))
                elif msg.get('role') == 'toolResult':
                    usages.append(UsageEntry(
                        agent='pi', model_id='', timestamp=timestamp,
                        input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
                        total_tokens=0, cost=0.0, cost_breakdown={}, session_id=session_id,
                        model_tool_calls=1, is_metric_only=True
                    ))
        except IOError as e:
            print(f"Error reading session file {session_file}: {e}")
        
        return usages
    
    @staticmethod
    def extract_usage() -> List[UsageEntry]:
        """Extract all usage data from Pi agent."""
        session_files = PiAgentExtractor.get_all_session_files()
        all_usages = []
        
        for session_file in session_files:
            usages = PiAgentExtractor.extract_usage_from_session(session_file)
            all_usages.extend(usages)
        
        return all_usages


class CodexExtractor:
    """Extract usage data from Codex rollout JSONL files."""
    
    @staticmethod
    def get_all_session_files() -> List[Path]:
        """Get all rollout JSONL files from Codex sessions directory."""
        base_dir = Path.home() / ".codex" / "sessions"
        session_files = []
        
        if not base_dir.exists():
            return session_files
        
        # Codex stores sessions in YYYY/MM/DD structure
        for year_dir in base_dir.iterdir():
            if year_dir.is_dir() and year_dir.name.isdigit():
                for month_dir in year_dir.iterdir():
                    if month_dir.is_dir() and month_dir.name.isdigit():
                        for day_dir in month_dir.iterdir():
                            if day_dir.is_dir():
                                for file in day_dir.glob("rollout-*.jsonl"):
                                    session_files.append(file)
        
        return session_files
    
    @staticmethod
    def extract_usage_from_session(session_file: Path) -> List[UsageEntry]:
        """Extract usage data from a Codex rollout session file."""
        usages = []
        current_model = 'unknown'
        current_session_id = None
        current_provider = None
        seen_token_events = set()
        
        try:
            with open(session_file, 'r') as f:
                for line in f:
                    try:
                        event = json.loads(line.strip())
                        event_type = event.get('type')

                        if event_type == 'session_meta':
                            payload = event.get('payload', {})
                            current_session_id = payload.get('id')
                            current_provider = payload.get('model_provider')

                        # Track model from turn_context
                        elif event_type == 'turn_context':
                            payload = event.get('payload', {})
                            current_model = payload.get('model', 'unknown')
                            current_provider = payload.get('model_provider', current_provider)
                        
                        # Extract token usage from token_count events
                        elif event_type == 'event_msg':
                            payload = event.get('payload', {})
                            if payload.get('type') == 'token_count':
                                info = payload.get('info', {})
                                last_usage = info.get('last_token_usage', {})
                                
                                if last_usage:
                                    timestamp = event.get('timestamp', '')
                                    turn_id = payload.get('turn_id')
                                    request_key = (turn_id, timestamp, tuple(sorted(last_usage.items())))
                                    if request_key in seen_token_events:
                                        continue
                                    seen_token_events.add(request_key)
                                    input_tok = last_usage.get('input_tokens', 0)
                                    output_tok = last_usage.get('output_tokens', 0)
                                    cache_read_tok = last_usage.get('cached_input_tokens', 0)
                                    cache_write_tok = last_usage.get('cache_write_input_tokens', 0)
                                    total_tok = last_usage.get('total_tokens', 0)
                                    
                                    usages.append(UsageEntry(
                                        agent='codex',
                                        model_id=current_model,
                                        timestamp=timestamp,
                                        input_tokens=input_tok,
                                        output_tokens=output_tok,
                                        cache_read_tokens=cache_read_tok,
                                        cache_write_tokens=cache_write_tok,
                                        total_tokens=total_tok,
                                        cost=0.0,
                                        cost_breakdown={},
                                        provider=current_provider,
                                        observed_provider=current_provider,
                                        is_aggregated=False,
                                        session_id=current_session_id,
                                        model_requests=1
                                    ))
                            elif payload.get('type') == 'task_started':
                                usages.append(UsageEntry(
                                    agent='codex', model_id=current_model,
                                    timestamp=event.get('timestamp', ''), input_tokens=0,
                                    output_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
                                    total_tokens=0, cost=0.0, cost_breakdown={},
                                    provider=current_provider, observed_provider=current_provider,
                                    session_id=current_session_id,
                                    model_turns=1, is_metric_only=True
                                ))
                        elif event_type == 'response_item':
                            payload = event.get('payload', {})
                            if payload.get('type') in ('function_call', 'custom_tool_call'):
                                usages.append(UsageEntry(
                                    agent='codex', model_id=current_model,
                                    timestamp=event.get('timestamp', ''), input_tokens=0,
                                    output_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
                                    total_tokens=0, cost=0.0, cost_breakdown={},
                                    provider=current_provider, observed_provider=current_provider,
                                    session_id=current_session_id,
                                    model_tool_calls=1, is_metric_only=True
                                ))
                    except json.JSONDecodeError:
                        continue
        except IOError as e:
            print(f"Error reading Codex session file {session_file}: {e}")
        
        return usages
    
    @staticmethod
    def extract_usage() -> List[UsageEntry]:
        """Extract all usage data from Codex."""
        session_files = CodexExtractor.get_all_session_files()
        
        if not session_files:
            return []
        
        all_usages = []
        for session_file in session_files:
            usages = CodexExtractor.extract_usage_from_session(session_file)
            all_usages.extend(usages)
        
        return all_usages


# ============================================================================
# DATA PROCESSING AND ANALYSIS
# ============================================================================

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


# Color mapping for agent headers
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


def print_single_agent_report(agent: str, usages: List[UsageEntry], 
                             stats: AgentStats, start_date: datetime.date, 
                             end_date: datetime.date, period_label: str):
    """Print detailed report for a single agent in pi.py format."""
    color = AGENT_COLORS.get(agent, Colors.reset)
    
    # Print header with color
    print_agent_header(agent)
    
    print(f"\n{color}Analysis Period: {start_date} to {end_date} ({period_label}){Colors.reset}")
    for warning in stats.scope_warnings:
        print(f"Warning: {warning}")
    
    if not usages:
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
    total_days = (end_date - start_date).days + 1
    
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
    if total_cache_tokens > 0:
        cache_ratio = stats.total_cache_read_tokens / total_cache_tokens
        print(f"Cache read ratio: {cache_ratio:.1%} ({stats.total_cache_read_tokens:,} / {total_cache_tokens:,})")
    
    if stats.total_cache_read_tokens > 0 and stats.total_cache_write_tokens > 0:
        efficiency = stats.total_cache_read_tokens / stats.total_cache_write_tokens
        print(f"Cache efficiency ratio: {efficiency:.1f}:1")
    
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


def print_summary_comparison(all_stats: Dict[str, AgentStats]):
    """Print comparison summary across multiple agents."""
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
    
    for agent, stats in all_stats.items():
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


# ============================================================================
# COMMAND LINE INTERFACE
# ============================================================================

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


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Eurysx: local usage intelligence for AI coding agents.'
    )
    parser.add_argument('-v', '--version', action='version',
                        version=f'%(prog)s {__version__}')
    
    # Agent selection - multiple values allowed
    parser.add_argument('--agent', nargs='+', 
                       choices=['claude-code', 'opencode', 'pi', 'codex', 'all'],
                       default='all',
                       help='Agent(s) to analyze. Use "all" for all detected agents, or list multiple: --agent claude-code opencode')
    
    # Time period flags
    period = parser.add_mutually_exclusive_group()
    period.add_argument('-w', '--weeks', type=_positive_int,
                        help='Last N weeks, including today')
    period.add_argument('-d', '--days', type=_positive_int,
                        help='Last N days, including today')
    period.add_argument('--month', type=_year_month, metavar='YYYY-MM',
                        help='Calendar month')
    period.add_argument('--quarter', type=_year_quarter, metavar='YYYY-QN',
                        help='Calendar quarter')
    period.add_argument('--year', type=_year, metavar='YYYY',
                        help='Calendar year')
    period.add_argument('--ytd', action='store_true', help='Year-to-date')
    parser.add_argument('--from', dest='start_date', type=_iso_date, metavar='YYYY-MM-DD',
                        help='Inclusive start date')
    parser.add_argument('--to', dest='end_date', type=_iso_date, metavar='YYYY-MM-DD',
                        help='Inclusive end date; requires --from')
    
    # Output options
    parser.add_argument('--output', type=str,
                       help='Save results to file (JSON format)')
    parser.add_argument('--refresh-pricing', action='store_true',
                       help='Force refresh of enabled remote pricing sources')
    
    args = parser.parse_args()
    if args.end_date and not args.start_date:
        parser.error('--to requires --from')
    if args.start_date and any((args.days, args.weeks, args.month, args.quarter,
                                args.year, args.ytd)):
        parser.error('--from cannot be combined with another period selector')
    if args.start_date and args.end_date and args.start_date > args.end_date:
        parser.error('--from must be on or before --to')
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


def main():
    """Main entry point."""
    args = parse_args()
    
    # Disable colors if outputting to file
    if args.output:
        Colors.disable()
    
    # Get date range
    start_date, end_date, period_label = get_date_range(args)
    is_all_time = start_date is None
    resolver = PricingResolver(force_refresh=args.refresh_pricing)
    preferences = PreferencesResolver()
    for warning in resolver.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    for warning in preferences.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    
    # Handle 'all' vs specific agents
    if 'all' in args.agent:
        if len(args.agent) > 1:
            print("Error: 'all' cannot be combined with other agents.")
            print("Usage: --agent all OR --agent claude-code opencode")
            return
        agents_to_analyze = AgentPaths.detect_agents()
        if not agents_to_analyze:
            print("No agents detected. Check if any agents are installed.")
            return
    else:
        agents_to_analyze = args.agent
    
    print(f"Analyzing agents: {', '.join(agents_to_analyze)}")
    
    # Collect data from each agent
    agent_data = {}
    for agent in agents_to_analyze:
        print(f"\nCollecting data from {agent}...")
        
        if agent == 'claude-code':
            usages = ClaudeCodeExtractor.extract_usage()
        elif agent == 'opencode':
            usages = OpenCodeExtractor.extract_usage()
        elif agent == 'pi':
            usages = PiAgentExtractor.extract_usage()
        elif agent == 'codex':
            usages = CodexExtractor.extract_usage()
        else:
            print(f"Unknown agent: {agent}")
            continue
        apply_pricing(usages, resolver, preferences)
        
        if usages:
            print(f"  Found {len(usages)} usage entries")
            agent_data[agent] = usages
        else:
            print(f"  No usage data found for {agent}")
    
    # Analyze each agent
    all_stats = {}
    for agent, usages in agent_data.items():
        print(f"\nAnalyzing {agent}...")
        
        display_start_date = start_date
        display_period_label = period_label
        if is_all_time and usages:
            dates = []
            for usage in usages:
                date = UsageAnalyzer.extract_date_from_timestamp(usage.timestamp)
                if date:
                    dates.append(date)
            
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
        
        # Print agent report (with color coding)
        print_single_agent_report(
            agent, usages, stats, display_start_date, end_date, display_period_label
        )
    
    # Print multi-agent comparison if analyzing multiple agents
    if len(agent_data) > 1:
        print_summary_comparison(all_stats)
    
    # Write JSON output if --output flag provided
    if args.output:
        output_result = {
            'analysis_period': {
                'start': str(start_date) if start_date else 'ALL TIME',
                'end': str(end_date),
                'label': period_label
            },
            'agents_analyzed': list(all_stats.keys()),
            'pricing': {
                'config_file': str(resolver.config_path),
                'sources': resolver.fetched_at,
                'warnings': resolver.warnings,
            },
            'preferences': {
                'config_file': str(preferences.config_path),
                'warnings': preferences.warnings,
            },
            'agent_stats': {}
        }
        
        for agent, stats in all_stats.items():
            output_result['agent_stats'][agent] = {
                'model_requests': stats.total_model_requests,
                'model_turns': stats.total_model_turns,
                'model_tool_calls': stats.total_model_tool_calls,
                'total_input_tokens': stats.total_input_tokens,
                'total_output_tokens': stats.total_output_tokens,
                'total_cache_read_tokens': stats.total_cache_read_tokens,
                'total_cache_write_tokens': stats.total_cache_write_tokens,
                'total_tokens': stats.total_tokens,
                'total_cost': stats.total_cost,
                'known_cost': stats.known_cost,
                'unknown_cost_count': stats.unknown_cost_count,
                'unknown_cost_tokens': stats.unknown_cost_tokens,
                'priced_token_coverage': stats.priced_token_coverage,
                'metered_tokens': stats.metered_tokens,
                'non_metered_tokens': stats.non_metered_tokens,
                'billing_mode_tokens': stats.billing_mode_tokens,
                'route_breakdown': stats.route_breakdown,
                'cost_status_counts': stats.cost_status_counts,
                'pricing_sources': sorted(stats.pricing_sources),
                'pricing_fetched_at': stats.pricing_fetched_at,
                'daily_cost': stats.daily_cost,
                'weekly_cost': stats.weekly_cost,
                'monthly_cost': stats.monthly_cost,
                'quarterly_cost': stats.quarterly_cost,
                'yearly_cost': stats.yearly_cost,
                'usage_entries': stats.usage_entries,
                'unique_models': list(stats.unique_models),
                'model_breakdown': stats.model_breakdown,
                'daily_activity': stats.daily_activity,
                'scope_warnings': stats.scope_warnings,
            }
        
        # Add combined summary if multiple agents
        if len(all_stats) > 1:
            combined_tokens = sum(s.total_tokens for s in all_stats.values())
            combined_cost = sum(s.total_cost for s in all_stats.values())
            combined_unknown_tokens = sum(s.unknown_cost_tokens for s in all_stats.values())
            combined_metered_tokens = sum(s.metered_tokens for s in all_stats.values())
            combined_non_metered_tokens = defaultdict(int)
            for stats in all_stats.values():
                for billing_mode, tokens in stats.non_metered_tokens.items():
                    combined_non_metered_tokens[billing_mode] += tokens
            output_result['combined_summary'] = {
                'total_tokens': combined_tokens,
                'total_cost': combined_cost,
                'known_cost': combined_cost,
                'unknown_cost_tokens': combined_unknown_tokens,
                'metered_tokens': combined_metered_tokens,
                'non_metered_tokens': dict(combined_non_metered_tokens),
                'priced_token_coverage': (
                    (combined_metered_tokens - combined_unknown_tokens) / combined_metered_tokens
                    if combined_metered_tokens else None
                ),
            }
        
        # Write to file
        import json as json_module
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json_module.dump(output_result, f, indent=2)
        print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    # Enable colors if output is to terminal
    if should_colorize():
        pass  # Colors already enabled
    else:
        Colors.disable()
    
    main()
