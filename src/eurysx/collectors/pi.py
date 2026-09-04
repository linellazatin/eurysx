"""Pi local history collector."""

import json
from pathlib import Path
from typing import Dict, List, Optional

from ..models import UsageEntry
from .sources import Source, fingerprint_paths


PARSER_VERSION = "2"


class PiAgentExtractor:
    """Extract usage data from Pi agent session files."""

    @staticmethod
    def get_all_session_files(home: Optional[Path] = None) -> List[Path]:
        """Get all session JSONL files from pi agent sessions directory."""
        base_dir = (home or Path.home()) / ".pi" / "agent" / "sessions"
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
        """Extract usage data from a session file. IOError propagates to the caller."""
        usages = []
        with open(session_file, 'r') as f:
            events = []
            for line in f:
                event = PiAgentExtractor.parse_session_line(line)
                if event:
                    events.append(event)
        by_id = {event.get('id'): event for event in events}
        project_id = next((event.get('cwd') for event in events if event.get('cwd')), None)
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
                    session_id=session_id, project_id=project_id,
                    model_requests=1, model_turns=1 if parent.get('role') == 'user' else 0
                ))
            elif msg.get('role') == 'toolResult':
                usages.append(UsageEntry(
                    agent='pi', model_id='', timestamp=timestamp,
                    input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0,
                    total_tokens=0, cost=0.0, cost_breakdown={}, session_id=session_id,
                    project_id=project_id,
                    model_tool_calls=1, is_metric_only=True
                ))

        return usages


def enumerate_sources(home: Optional[Path] = None) -> List[Source]:
    """One source per Pi session file."""
    sources = []
    for session_file in sorted(PiAgentExtractor.get_all_session_files(home)):
        sources.append(Source(
            key=f"pi:{session_file}",
            fingerprint=fingerprint_paths([session_file]),
            parser_version=PARSER_VERSION,
            parse=lambda path=session_file: PiAgentExtractor.extract_usage_from_session(path),
        ))
    return sources


def collect(home: Optional[Path] = None) -> List[UsageEntry]:
    return [
        entry
        for source in enumerate_sources(home)
        for entry in source.parse()
    ]
