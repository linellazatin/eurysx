"""Project-local Eurysx path helpers."""

import os
from pathlib import Path
from typing import Dict, Optional, Tuple


def get_eurysx_dirs(environ: Optional[Dict[str, str]] = None,
                    root: Optional[Path] = None) -> Tuple[Path, Path]:
    """Return checkout-local configuration and cache directories without creating them."""
    environ = os.environ if environ is None else environ
    root = root or Path.cwd()
    config_override = environ.get("EURYSX_CONFIG_DIR")
    cache_override = environ.get("EURYSX_CACHE_DIR")
    return (
        Path(config_override).expanduser() if config_override else root / "config",
        Path(cache_override).expanduser() if cache_override else root / "cache",
    )


def get_eurysx_data_dir(environ: Optional[Dict[str, str]] = None,
                        root: Optional[Path] = None) -> Path:
    environ = os.environ if environ is None else environ
    root = root or Path.cwd()
    override = environ.get("EURYSX_DATA_DIR")
    return Path(override).expanduser() if override else root / "data"
