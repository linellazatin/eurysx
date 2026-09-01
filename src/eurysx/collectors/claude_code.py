"""Claude Code local history collector."""

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from ..models import UsageEntry, UsageMetrics
from .paths import AgentPaths


class ClaudeCodeExtractor:
    """Extract usage data from Claude Code stats-cache.json."""
    
    @staticmethod
    def extract_usage(home: Optional[Path] = None) -> List[UsageEntry]:
        """Extract usage data from Claude Code JSON cache."""
        cache_path = AgentPaths.claude_code(home)
        if not cache_path:
            return []
        
        try:
            with open(cache_path, 'r') as f:
                stats = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error reading Claude Code data: {e}")
            return []
        
        usages = []
        transcript_metrics = ClaudeCodeExtractor.extract_transcript_metrics(home)
        
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
    def extract_transcript_metrics(home: Optional[Path] = None) -> Dict[str, UsageMetrics]:
        metrics = defaultdict(UsageMetrics)
        for path in AgentPaths.claude_transcripts(home):
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


def collect(home: Optional[Path] = None) -> List[UsageEntry]:
    return ClaudeCodeExtractor.extract_usage(home)
