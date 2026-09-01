"""Pi local history collector."""

import json
from pathlib import Path
from typing import Dict, List, Optional

from ..models import UsageEntry


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
    def extract_usage(home: Optional[Path] = None) -> List[UsageEntry]:
        """Extract all usage data from Pi agent."""
        session_files = PiAgentExtractor.get_all_session_files(home)
        all_usages = []
        
        for session_file in session_files:
            usages = PiAgentExtractor.extract_usage_from_session(session_file)
            all_usages.extend(usages)
        
        return all_usages



def collect(home: Optional[Path] = None) -> List[UsageEntry]:
    return PiAgentExtractor.extract_usage(home)

