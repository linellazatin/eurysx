"""Fixed collector dispatcher for supported coding agents."""
from pathlib import Path
from typing import List, Optional

from . import claude_code, codex, opencode, pi
from .paths import AgentPaths
from .sources import Source, fingerprint_paths


SOURCES = {
    "claude-code": claude_code.enumerate_sources,
    "opencode": opencode.enumerate_sources,
    "pi": pi.enumerate_sources,
    "codex": codex.enumerate_sources,
}


def detect_agents(home: Optional[Path] = None) -> List[str]:
    checks = {
        "claude-code": AgentPaths.claude_code,
        "opencode": AgentPaths.opencode,
        "pi": AgentPaths.pi_agent,
        "codex": AgentPaths.codex,
    }
    return [agent for agent, available in checks.items() if available(home)]


def collect_sources(agent: str, home: Optional[Path] = None) -> List[Source]:
    return SOURCES[agent](home)


def collect(agent: str, home: Optional[Path] = None):
    return [
        entry
        for source in collect_sources(agent, home)
        for entry in source.parse()
    ]
