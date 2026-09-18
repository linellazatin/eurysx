"""User-supplied provider aggregate report imports (not harness collectors)."""

from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import anthropic_usage

# Fixed reader registry keyed by the preferences.jsonc `type` value.
IMPORT_SOURCES: Dict[str, Callable] = {
    "anthropic-usage-report": anthropic_usage.read_usage_report,
    "anthropic-cost-report": anthropic_usage.read_cost_report,
}

IMPORT_PARSER_VERSIONS = {kind: anthropic_usage.PARSER_VERSION for kind in IMPORT_SOURCES}


def entry_files(entry: Dict[str, str]) -> List[Path]:
    """Files a declared glob currently matches; empty means unreachable."""
    pattern = Path(entry["path"]).expanduser()
    if pattern.is_file():
        return [pattern]
    return sorted(pattern.parent.glob(pattern.name))


def enumerate_sources(entry: Dict[str, str],
                      warnings: Optional[List[str]] = None) -> List:
    """One source per declared import entry: the whole glob is replaced atomically.

    No source is returned when the glob matches nothing, so stored rows survive as
    last-good data; the caller reports the unreachable entry.
    """
    if entry["type"] not in IMPORT_SOURCES:
        raise ValueError(f"unsupported aggregate import type {entry['type']}")
    files = entry_files(entry)
    if not files:
        return []
    return [anthropic_usage.source(entry["type"], entry["path"], entry["scope"], files,
                                   warnings)]
