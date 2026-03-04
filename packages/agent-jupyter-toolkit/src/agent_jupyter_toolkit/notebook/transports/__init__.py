"""
Notebook transport implementations.

This package provides concrete implementations of the NotebookDocumentTransport
protocol for different storage and collaboration backends:

- LocalFileDocumentTransport: Local .ipynb files via nbformat
- ContentsApiDocumentTransport: Remote Jupyter server via Contents API
- CollabYjsDocumentTransport: Collaborative editing via Yjs/CRDT
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .contents import ContentsApiDocumentTransport
from .local_file import LocalFileDocumentTransport

if TYPE_CHECKING:
    from .collab import CollabYjsDocumentTransport

__all__ = [
    "LocalFileDocumentTransport",
    "ContentsApiDocumentTransport",
    "CollabYjsDocumentTransport",
]


def __getattr__(name: str) -> Any:
    if name == "CollabYjsDocumentTransport":
        try:
            cls = getattr(import_module(f"{__name__}.collab"), name)
        except Exception as exc:  # optional dependency/runtime issues
            raise ImportError(
                "CollabYjsDocumentTransport requires collaboration dependencies "
                "(pycrdt and jupyter_ydoc)."
            ) from exc
        globals()[name] = cls
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
