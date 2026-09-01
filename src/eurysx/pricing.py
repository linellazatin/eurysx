"""Pricing and preference resolution."""

import json
import os
import re
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .models import UsageEntry
from .paths import get_eurysx_dirs


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
