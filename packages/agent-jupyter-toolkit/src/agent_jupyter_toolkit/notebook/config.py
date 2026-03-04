from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path


def _parse_allowlist(env_var: str = "JAT_NOTEBOOK_ALLOWLIST") -> tuple[Path, ...]:
    raw = os.getenv(env_var, "")
    if not raw.strip():
        # default to CWD if nothing provided
        return (Path.cwd().resolve(),)
    paths: list[Path] = []
    for frag in raw.split(","):
        p = Path(frag.strip()).expanduser().resolve()
        if p.exists():
            paths.append(p)
        else:
            # allow non-existent targets (e.g., new files) – parent must exist and be allowed
            parent = p.parent
            if not parent.exists():
                logging.warning(f"Allowlist parent directory does not exist: {parent}")
            paths.append(p)
    return tuple(paths) if paths else (Path.cwd().resolve(),)


def _parse_timeout(env_var: str = "JAT_NOTEBOOK_TIMEOUT", default: int = 20) -> int:
    try:
        v = int(os.getenv(env_var, str(default)))
        if v <= 0:
            logging.warning(f"Timeout value {v} is not positive. Using default {default}.")
            return default
        return v
    except Exception as e:
        logging.warning(f"Failed to parse timeout from env: {e}. Using default {default}.")
        return default


@dataclass(frozen=True)
class Config:
    # Comma-separated allowlist; defaults to CWD
    allowed_roots: tuple[Path, ...] = _parse_allowlist()
    # Seconds; defaults to 20
    execution_timeout_sec: int = _parse_timeout()
    # Warm-up imports for the kernel (empty string to disable)
    kernel_prewarm: str = os.getenv(
        "JAT_NOTEBOOK_KERNEL_PREWARM",
        "import pandas as pd; import numpy as np; import json, os, sys",
    )


CFG = Config()
