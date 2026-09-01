"""OpenCode local history collector."""

import json
import sqlite3
from pathlib import Path
from typing import List, Optional

from ..models import UsageEntry
from .paths import AgentPaths


class OpenCodeExtractor:
    """Extract usage and event metrics from OpenCode SQLite storage."""

    @staticmethod
    def extract_usage(home: Optional[Path] = None) -> List[UsageEntry]:
        db_path = AgentPaths.opencode(home)
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


def collect(home: Optional[Path] = None) -> List[UsageEntry]:
    return OpenCodeExtractor.extract_usage(home)
