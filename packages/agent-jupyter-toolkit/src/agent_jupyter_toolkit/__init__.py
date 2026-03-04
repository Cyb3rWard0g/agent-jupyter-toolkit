"""
Jupyter Agent Toolkit.

A comprehensive toolkit for building AI agents that can interact with Jupyter notebooks
and kernels. Provides abstractions for notebook manipulation and kernel execution.

Main Packages:
- notebook: High-level notebook manipulation and transport abstractions
- kernel: Jupyter kernel integration and execution management
- utils: Common utilities and helper functions
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

__all__ = ["kernel", "notebook", "utils"]

if TYPE_CHECKING:
    from . import kernel, notebook, utils


def __getattr__(name: str) -> Any:
    if name in __all__:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
