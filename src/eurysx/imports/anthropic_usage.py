"""Explicit-file readers for Anthropic organization Usage & Cost reports.

Not a harness collector: the input is a report file the user downloaded themselves,
so no credentials are read and no request is made. Nothing here returns a UsageEntry,
which is what keeps imported aggregates out of pricing and out of local cost lanes.

REPORT_KEYS and COST_KEYS are provisional: pinned against Anthropic's published
endpoint documentation, awaiting a live saved response. See
tests/fixtures/anthropic_usage/README.md.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..collectors.sources import Source, fingerprint_paths
from ..models import AggregateImportRow

PARSER_VERSION = "1"

# Logical field -> key path inside one report bucket.
REPORT_KEYS = {
    "items": ("data",),
    "date": ("time_range", "start_date"),
    "bucket_end": ("time_range", "end_date"),
    "results": ("results",),
    "model": ("model",),
    "workspace_id": ("workspace_id",),
    "service_tier": ("service_tier",),
    "context_window": ("context_window",),
    "inference_geo": ("inference_geo",),
    "speed": ("speed",),
    "input_uncached": ("iterations", "input_tokens"),
    "input_cached": ("iterations", "cache_read_input_tokens"),
    "cache_creation": ("iterations", "cache_creation_input_tokens"),
    "output": ("iterations", "output_tokens"),
}

DEDUPE_KEY = ("date", "model", "workspace_id", "service_tier",
              "context_window", "inference_geo", "speed", "cost_type")

# Cost reports carry cents, not tokens. Anthropic sends costs as decimal strings in the
# lowest unit (cents), so conversion is exact: Decimal(cents) / 100.
COST_KEYS = {
    "items": ("data",),
    "date": ("time_range", "start_date"),
    "bucket_end": ("time_range", "end_date"),
    "results": ("results",),
    "cost_usd_cents": ("cost",),
    "cost_type": ("type",),
    "model": ("description", "model"),
    "workspace_id": ("workspace_id",),
    "inference_geo": ("description", "inference_geo"),
}

_VALUE_FIELDS = ("input_uncached_tokens", "input_cached_tokens", "cache_creation_tokens",
                 "output_tokens", "cost_usd")


def _dig(payload: Any, keys: Sequence[str]) -> Any:
    for key in keys:
        if not isinstance(payload, dict):
            return None
        payload = payload.get(key)
    return payload


def _read(item: Dict[str, Any], keys: Dict[str, Sequence[str]], name: str) -> Any:
    return _dig(item, keys[name])


def _date(value: Any) -> str:
    """Bucket boundary as YYYY-MM-DD; the API sends RFC 3339 timestamps."""
    return str(value or "")[:10]


def _optional_int(value: Any) -> Optional[int]:
    return None if value is None else int(value)


def _ingest_date(ingested_at: str) -> str:
    return ingested_at[:10]


def read_usage_report(payload: Dict[str, Any], *, source_file: str, source_mtime: str,
                      scope_label: str, ingested_at: str) -> List[AggregateImportRow]:
    """Normalize one saved usage_report/messages response into aggregate rows."""
    items = _dig(payload, REPORT_KEYS["items"])
    if not isinstance(items, list) or not items:
        raise ValueError(f"{source_file} is not an Anthropic usage report response")
    today = _ingest_date(ingested_at)
    rows = []
    for item in items:
        day = _date(_read(item, REPORT_KEYS, "date"))
        if not day:
            raise ValueError(f"{source_file} has a usage bucket without a start date")
        for bucket in _read(item, REPORT_KEYS, "results") or []:
            model = _read(bucket, REPORT_KEYS, "model")
            rows.append(AggregateImportRow(
                source_kind="anthropic-usage-report",
                date=day,
                bucket_end=_date(_read(item, REPORT_KEYS, "bucket_end")) or None,
                model=model if isinstance(model, str) else None,
                workspace_id=_read(bucket, REPORT_KEYS, "workspace_id"),
                service_tier=_read(bucket, REPORT_KEYS, "service_tier"),
                context_window=_read(bucket, REPORT_KEYS, "context_window"),
                inference_geo=_read(bucket, REPORT_KEYS, "inference_geo"),
                speed=_read(bucket, REPORT_KEYS, "speed"),
                input_uncached_tokens=_optional_int(_read(bucket, REPORT_KEYS, "input_uncached")),
                input_cached_tokens=_optional_int(_read(bucket, REPORT_KEYS, "input_cached")),
                cache_creation_tokens=_optional_int(_read(bucket, REPORT_KEYS, "cache_creation")),
                output_tokens=_optional_int(_read(bucket, REPORT_KEYS, "output")),
                scope_label=scope_label,
                source_file=str(source_file),
                source_mtime=source_mtime,
                ingested_at=ingested_at,
                parser_version=PARSER_VERSION,
                complete=day < today,
            ))
    return rows


def _differs(first: AggregateImportRow, second: AggregateImportRow) -> bool:
    return any(getattr(first, name) != getattr(second, name) for name in _VALUE_FIELDS)


def _usd(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    return Decimal(str(value)) / Decimal(100)


def read_cost_report(payload: Dict[str, Any], *, source_file: str, source_mtime: str,
                     scope_label: str, ingested_at: str) -> List[AggregateImportRow]:
    """Normalize one saved cost_report response: reported USD, never tokens."""
    items = _dig(payload, COST_KEYS["items"])
    if not isinstance(items, list) or not items:
        raise ValueError(f"{source_file} is not an Anthropic cost report response")
    today = _ingest_date(ingested_at)
    rows = []
    for item in items:
        day = _date(_read(item, COST_KEYS, "date"))
        if not day:
            raise ValueError(f"{source_file} has a cost bucket without a start date")
        for bucket in _read(item, COST_KEYS, "results") or []:
            model = _read(bucket, COST_KEYS, "model")
            rows.append(AggregateImportRow(
                source_kind="anthropic-cost-report",
                date=day,
                bucket_end=_date(_read(item, COST_KEYS, "bucket_end")) or None,
                model=model if isinstance(model, str) else None,
                workspace_id=_read(bucket, COST_KEYS, "workspace_id"),
                inference_geo=_read(bucket, COST_KEYS, "inference_geo"),
                cost_usd=_usd(_read(bucket, COST_KEYS, "cost_usd_cents")),
                cost_type=_read(bucket, COST_KEYS, "cost_type"),
                scope_label=scope_label,
                source_file=str(source_file),
                source_mtime=source_mtime,
                ingested_at=ingested_at,
                parser_version=PARSER_VERSION,
                complete=day < today,
            ))
    return rows


def _mtime(files: List[Path]) -> str:
    """Newest file mtime is the documented freshness proxy for a saved report."""
    stamps = [path.stat().st_mtime for path in files if path.exists()]
    if not stamps:
        return ""
    return datetime.fromtimestamp(max(stamps), timezone.utc).isoformat()


def source(kind: str, declared_path: str, scope_label: str,
           files: List[Path], warnings: Optional[List[str]] = None) -> Source:
    """One source per declared entry: the whole glob of pages replaces atomically."""
    reader = {"anthropic-usage-report": read_usage_report,
              "anthropic-cost-report": read_cost_report}[kind]
    keys = {"anthropic-usage-report": REPORT_KEYS,
            "anthropic-cost-report": COST_KEYS}[kind]
    mtime = _mtime(files)
    note = warnings if warnings is not None else []

    def parse() -> List[AggregateImportRow]:
        ingested_at = datetime.now(timezone.utc).isoformat()
        rows: List[AggregateImportRow] = []
        for path in files:
            payload = json.loads(path.read_text())
            parsed = reader(payload, source_file=str(path), source_mtime=mtime,
                            scope_label=scope_label, ingested_at=ingested_at)
            if not parsed:
                raise ValueError(f"{path} is not an Anthropic {kind} response")
            if payload.get("has_more"):
                note.append(
                    f"aggregate import for {scope_label} ends on a truncated page "
                    f"(has_more=true) in {path.name}; save the remaining pages into the "
                    "same declared path so totals are complete.")
            rows.extend(parsed)
        kept, conflicts = normalize_rows(rows)
        if conflicts:
            note.append(
                f"aggregate import for {scope_label} contains {conflicts} duplicate "
                "bucket(s) with disagreeing values; the first file's value was kept.")
        return kept

    return Source(key=f"{kind}:{Path(declared_path).expanduser()}",
                  fingerprint=fingerprint_paths(files), parser_version=PARSER_VERSION,
                  parse=parse)


def normalize_rows(rows: Sequence[AggregateImportRow]) -> Tuple[List[AggregateImportRow], int]:
    """Deduplicate one source's rows: first file wins, disagreements are counted."""
    seen: Dict[tuple, AggregateImportRow] = {}
    kept: List[AggregateImportRow] = []
    conflicts = 0
    for row in rows:
        identity = tuple(getattr(row, field) for field in DEDUPE_KEY)
        previous = seen.get(identity)
        if previous is None:
            seen[identity] = row
            kept.append(row)
        elif _differs(previous, row):
            conflicts += 1
    return kept, conflicts
