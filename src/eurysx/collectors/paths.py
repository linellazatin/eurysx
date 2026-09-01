"""Known local history locations for supported agents."""

from pathlib import Path
from typing import List, Optional


class AgentPaths:
    @staticmethod
    def _home(home: Optional[Path] = None) -> Path:
        return home or Path.home()

    @classmethod
    def claude_code(cls, home: Optional[Path] = None) -> Optional[Path]:
        path = cls._home(home) / ".claude" / "stats-cache.json"
        return path if path.exists() else None

    @classmethod
    def claude_transcripts(cls, home: Optional[Path] = None) -> List[Path]:
        return list((cls._home(home) / ".claude").glob("**/*.jsonl"))

    @classmethod
    def opencode(cls, home: Optional[Path] = None) -> Optional[Path]:
        path = cls._home(home) / ".local" / "share" / "opencode" / "opencode.db"
        return path if path.exists() else None

    @classmethod
    def pi_agent(cls, home: Optional[Path] = None) -> Optional[Path]:
        path = cls._home(home) / ".pi" / "agent" / "sessions"
        return path if path.exists() and any(path.iterdir()) else None

    @classmethod
    def codex(cls, home: Optional[Path] = None) -> Optional[Path]:
        path = cls._home(home) / ".codex" / "sessions"
        return path if path.exists() and any(path.glob("*/*/*/rollout-*.jsonl")) else None
