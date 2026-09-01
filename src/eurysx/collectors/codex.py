"""Codex local history collector."""

import json
from pathlib import Path
from typing import List, Optional

from ..models import UsageEntry


class CodexExtractor:
    """Extract usage data from Codex rollout JSONL files."""
    
    @staticmethod
    def get_all_session_files(home: Optional[Path] = None) -> List[Path]:
        """Get all rollout JSONL files from Codex sessions directory."""
        base_dir = (home or Path.home()) / ".codex" / "sessions"
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
    def extract_usage(home: Optional[Path] = None) -> List[UsageEntry]:
        """Extract all usage data from Codex."""
        session_files = CodexExtractor.get_all_session_files(home)
        
        if not session_files:
            return []
        
        all_usages = []
        for session_file in session_files:
            usages = CodexExtractor.extract_usage_from_session(session_file)
            all_usages.extend(usages)
        
        return all_usages


def collect(home: Optional[Path] = None) -> List[UsageEntry]:
    return CodexExtractor.extract_usage(home)
