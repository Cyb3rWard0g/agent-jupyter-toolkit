"""
Notebook transport factory and configuration utilities.

This module provides factory functions for creating appropriate NotebookDocumentTransport
instances based on configuration parameters. It supports local file, remote Contents API,
and collaborative Yjs transports, with automatic selection based on provided parameters.
"""

from __future__ import annotations

import json
from typing import Any, cast

from .transport import NotebookDocumentTransport
from .transports.contents import ContentsApiDocumentTransport
from .transports.local_file import LocalFileDocumentTransport


def _parse_headers(headers_json: str | None) -> dict[str, str] | None:
    if not headers_json:
        return None
    try:
        obj = json.loads(headers_json)
        if isinstance(obj, dict):
            # coerce keys/values to str
            return {str(k): str(v) for k, v in obj.items()}
    except Exception:
        pass
    return None


def make_document_transport(
    mode: str,
    *,
    local_path: str | None,
    remote_base: str | None,
    remote_path: str | None,
    token: str | None,
    headers_json: str | None,
    prefer_collab: bool = False,
    create_if_missing: bool = False,
    local_autosave_delay: float | None = None,
) -> NotebookDocumentTransport:
    """
    Create a NotebookDocumentTransport based on runtime configuration.

    This factory function selects the appropriate transport implementation
    based on the mode and available configuration. It handles local file
    access, remote Jupyter server access via Contents API, and collaborative
    editing via Yjs when available.

    Args:
        mode: Transport mode - "local" for file system, "server" for remote Jupyter
        local_path: File system path to .ipynb file (required for local mode)
        remote_base: Jupyter server base URL (e.g., http://localhost:8888)
        remote_path: Notebook path relative to server root (e.g., notebooks/analysis.ipynb)
        token: Optional API token for server authentication
        headers_json: Optional JSON string of additional headers (cookies, XSRF, etc.)
        prefer_collab: If True and available, use collaborative Yjs transport
        create_if_missing: If True, create the notebook file/resource if it doesn't exist
        local_autosave_delay: Optional debounce delay (seconds) for local file writes

    Returns:
        NotebookDocumentTransport: A transport implementation ready for use

    Raises:
        ImportError: If collaborative transport is requested but dependencies are missing

    Example:
        ```python
        # Local file transport
        transport = make_document_transport(
            mode="local",
            local_path="/path/to/notebook.ipynb",
            create_if_missing=True
        )

        # Remote server transport
        transport = make_document_transport(
            mode="server",
            remote_base="http://localhost:8888",
            remote_path="analysis.ipynb",
            token="your-token-here"
        )

        # Collaborative transport (if available)
        transport = make_document_transport(
            mode="server",
            remote_base="http://localhost:8888",
            remote_path="shared.ipynb",
            prefer_collab=True
        )
        ```

    Note:
        If no valid configuration is provided, returns a no-op transport that
        provides minimal functionality without persistence (useful for execution-only
        scenarios).
    """
    mode = (mode or "").lower()

    if mode == "local" and local_path:
        return LocalFileDocumentTransport(local_path, autosave_delay=local_autosave_delay)

    if mode == "server" and remote_path:
        headers = _parse_headers(headers_json)

        if prefer_collab and remote_base:
            # Lazy import to avoid hard dependency on pycrdt for kernel-only use.
            from .transports.collab import CollabYjsDocumentTransport

            # Use collaborative transport with built-in creation
            return CollabYjsDocumentTransport(
                remote_base,
                remote_path,
                token=token,
                headers=headers,
                create_if_missing=create_if_missing,
            )

        if remote_base:
            return ContentsApiDocumentTransport(
                remote_base,
                remote_path,
                token=token,
                headers=headers,
                create_if_missing=create_if_missing,
            )

    # Missing configuration fallback (execution-only)
    class _NoopDoc:
        """
        No-op document transport that provides minimal functionality without persistence.

        Used as a fallback when no valid configuration is provided. This allows
        execution-only scenarios where notebook structure isn't needed, but the
        transport interface must still be satisfied.

        All methods are no-ops except fetch() which returns a minimal empty notebook.
        """

        async def start(self) -> None:
            """Initialize (no-op)."""
            ...

        async def stop(self) -> None:
            """Stop (no-op)."""
            ...

        async def is_connected(self) -> bool:
            """Always returns True."""
            return True

        async def fetch(self) -> dict[str, Any]:
            """Return minimal empty notebook structure."""
            return {"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}

        async def get_cell(self, index: int) -> dict[str, Any]:
            """No-op transport has no cells."""
            raise IndexError(f"get_cell: index {index} out of range (notebook is empty)")

        async def cell_count(self) -> int:
            """No-op transport always has 0 cells."""
            return 0

        async def get_cell_source(self, index: int) -> str:
            """No-op transport has no cells."""
            raise IndexError(f"get_cell_source: index {index} out of range (notebook is empty)")

        async def save(self, content: dict[str, Any]) -> None:
            """Save (no-op)."""
            ...

        async def append_code_cell(self, *args, **kwargs) -> int:
            """Append code cell (no-op), returns index 0."""
            return 0

        async def insert_code_cell(self, *args, **kwargs) -> None:
            """Insert code cell (no-op)."""
            ...

        async def update_cell_outputs(self, *args, **kwargs) -> None:
            """Update cell outputs (no-op)."""
            ...

        async def append_markdown_cell(self, *args, **kwargs) -> int:
            """Append markdown cell (no-op), returns index 0."""
            return 0

        async def insert_markdown_cell(self, *args, **kwargs) -> None:
            """Insert markdown cell (no-op)."""
            ...

        async def set_cell_source(self, *args, **kwargs) -> None:
            """Set cell source (no-op)."""
            ...

        async def delete_cell(self, *args, **kwargs) -> None:
            """Delete cell (no-op)."""
            ...

        def on_change(self, callback) -> None:
            """Register change callback (no-op)."""
            ...

    # Make mypy happy about the return type
    return cast(NotebookDocumentTransport, _NoopDoc())
