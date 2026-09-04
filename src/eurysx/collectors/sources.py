"""Shared source-descriptor helpers for collectors."""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List


@dataclass(frozen=True)
class Source:
    """One independently refreshable raw source with a deferred parse."""

    key: str
    fingerprint: str
    parser_version: str
    parse: Callable[[], List]


def fingerprint_paths(paths: Iterable[Path]) -> str:
    """Stat digest of the files backing a source.

    ponytail: a rewrite that keeps size and mtime_ns exactly is missed;
    upgrade to a content hash if a harness is caught doing that.
    """
    digest = hashlib.sha256()
    for path in sorted({Path(candidate) for candidate in paths}):
        try:
            stat = os.stat(path)
            digest.update(f"{path}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
        except OSError:
            digest.update(f"{path}\0missing\n".encode())
    return digest.hexdigest()
