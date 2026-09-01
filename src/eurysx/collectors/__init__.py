"""Fixed collector dispatcher for supported coding agents."""
from pathlib import Path
from typing import List, Optional

from . import claude_code, codex, opencode, pi
from .paths import AgentPaths


COLLECTORS = {
    "claude-code": claude_code.collect,
    "opencode": opencode.collect,
    "pi": pi.collect,
    "codex": codex.collect,
}


def detect_agents(home: Optional[Path] = None) -> List[str]:
    checks = {
        "claude-code": AgentPaths.claude_code,
        "opencode": AgentPaths.opencode,
        "pi": AgentPaths.pi_agent,
        "codex": AgentPaths.codex,
    }
    return [agent for agent, available in checks.items() if available(home)]


def collect(agent: str, home: Optional[Path] = None):
    return COLLECTORS[agent](home)
