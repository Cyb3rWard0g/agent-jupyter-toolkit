"""
Notebook document transport protocol.

This module defines a runtime-checkable Protocol for interacting with a notebook
"model" (cells + metadata) independently of storage/transport. Implementations
may target:

- Local files via nbformat (read/write on disk)
- Remote Jupyter Contents API (HTTP GET/PUT)
- Jupyter Collaboration (Yjs over WebSocket)

Design goals
------------
- Asynchronous & transport-agnostic surface.
- Minimal, composable operations for cells and whole-model I/O.
- Idempotent lifecycle methods (`start`/`stop`) to simplify orchestration.
- Zero-based indices for all cell addressing, with explicit range semantics.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import (
    Any,
    Protocol,
    TypedDict,
    runtime_checkable,
)


class NbCellOutput(TypedDict, total=False):
    """nbformat-like output dict shape used by `update_cell_outputs`."""

    output_type: str
    name: str
    text: str
    data: dict[str, Any]
    metadata: dict[str, Any]
    ename: str
    evalue: str
    traceback: list[str]


@runtime_checkable
class NotebookDocumentTransport(Protocol):
    """
    Transport abstraction over a notebook document.

    Implementations MUST be safe to use from a single asyncio task at a time.
    If they perform multiple-step mutations (e.g., fetch→mutate→save), they
    SHOULD serialize such sequences internally.

    Lifecycle:
        - `start()` / `stop()` SHOULD be idempotent.
        - `is_connected()` reports whether the transport is ready for use.

    Indexing:
        - All cell indices are zero-based.
        - Methods that accept an index MUST raise IndexError when out-of-range.

    Errors:
        - Network/IO failures SHOULD raise `RuntimeError` with detail.
        - Type/shape violations SHOULD raise `TypeError` or `ValueError`.
    """

    async def start(self) -> None:
        """
        Initialize any required resources (e.g., open file, create HTTP/WS session).
        Idempotent: calling multiple times should be a no-op after success.

        Raises:
            RuntimeError: if the transport cannot be initialized.
        """
        ...

    async def stop(self) -> None:
        """
        Release resources acquired by `start()` (e.g., close sessions/sockets).
        Idempotent and fault-tolerant: may be called even if `start()` failed part-way.
        """
        ...

    async def is_connected(self) -> bool:
        """
        Return True if the transport is ready for model operations.

        Note:
            A True value does not imply the backing storage is reachable forever,
            only that the transport is currently initialized.
        """
        ...

    async def fetch(self) -> dict[str, Any]:
        """
        Return the notebook as an nbformat-like dict.

        Expected shape:
            {
              "cells": [ ... ],
              "metadata": { ... },
              "nbformat": 4,
              "nbformat_minor": 5
            }

        Raises:
            RuntimeError: on IO/network errors or unexpected server responses.
        """
        ...

    async def save(self, content: dict[str, Any]) -> None:
        """
        Persist the entire notebook model.

        Args:
            content: An nbformat-like dict as returned by `fetch()`.

        Raises:
            RuntimeError: on IO/network errors or unexpected server responses.
            TypeError: if `content` is not a dict.
        """
        ...

    # ---------- cell mutations: code ----------

    async def append_code_cell(
        self,
        source: str,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> int:
        """
        Append a code cell to the end of the notebook.

        Args:
            source: Source code for the cell.
            metadata: Optional nbformat-style metadata to merge into the cell.
            tags: Optional list of tags to union into metadata["tags"].

        Returns:
            The zero-based index of the newly appended cell.
        """
        ...

    async def insert_code_cell(
        self,
        index: int,
        source: str,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """
        Insert a code cell at a specific position.

        Args:
            index: Target position in [0..len] (len means append).
            source: Source code for the cell.
            metadata: Optional nbformat-style metadata.
            tags: Optional tags for metadata["tags"].

        Raises:
            IndexError: if index is not in [0..len].
        """
        ...

    async def update_cell_outputs(
        self,
        index: int,
        outputs: list[NbCellOutput],
        execution_count: int | None,
    ) -> None:
        """
        Replace outputs and execution count of the code cell at `index`.

        Args:
            index: Code cell index in [0..len-1].
            outputs: List of nbformat-like output dicts.
            execution_count: New execution count (or None).

        Raises:
            IndexError: if index is out of range.
            TypeError: if outputs is not a list of dict-like items.
            TypeError: if the target cell is not a code cell (implementation-dependent).
        """
        ...

    async def append_markdown_cell(
        self,
        source: str,
        tags: list[str] | None = None,
    ) -> int:
        """
        Append a markdown cell to the end of the notebook.

        Args:
            source: Markdown text.
            tags: Optional tags for metadata["tags"].

        Returns:
            The zero-based index of the newly appended cell.
        """
        ...

    async def insert_markdown_cell(
        self,
        index: int,
        source: str,
        tags: list[str] | None = None,
    ) -> None:
        """
        Insert a markdown cell at a specific position.

        Args:
            index: Target position in [0..len] (len means append).
            source: Markdown text.
            tags: Optional tags for metadata["tags"].

        Raises:
            IndexError: if index is not in [0..len].
        """
        ...

    async def set_cell_source(self, index: int, source: str) -> None:
        """
        Replace the source text of the cell at `index` (code or markdown).

        Args:
            index: Cell index in [0..len-1].
            source: New source text.

        Raises:
            IndexError: if index is out of range.
        """
        ...

    async def get_cell(self, index: int) -> dict[str, Any]:
        """
        Return the cell at `index` as an nbformat-like dict.

        Implementations backed by a real-time CRDT (e.g. Yjs) SHOULD read the
        single cell directly from the shared document without serialising the
        entire notebook, making this cheaper than ``fetch()``.

        For file-backed or HTTP-backed transports a full ``fetch()`` followed
        by indexing is an acceptable implementation.

        Args:
            index: Zero-based cell index.

        Returns:
            Dict with at least ``cell_type``, ``source``, ``metadata``, and,
            for code cells, ``outputs`` and ``execution_count``.

        Raises:
            IndexError: if index is not in [0..len-1].
        """
        ...

    async def cell_count(self) -> int:
        """
        Return the number of cells currently in the notebook.

        Implementations backed by a CRDT SHOULD return the count directly
        from the shared document.  File-/HTTP-backed transports MAY read
        and parse the full notebook if no lighter mechanism is available.

        Returns:
            Number of cells (>= 0).
        """
        ...

    async def get_cell_source(self, index: int) -> str:
        """
        Return the source text of the cell at `index`.

        This is a convenience shortcut equivalent to
        ``(await self.get_cell(index))["source"]`` but implementations MAY
        provide a more efficient path (e.g. reading only the ``YText`` for
        the cell).

        Args:
            index: Zero-based cell index.

        Returns:
            The cell's source string.

        Raises:
            IndexError: if index is not in [0..len-1].
        """
        ...

    async def delete_cell(self, index: int) -> None:
        """
        Delete the cell at `index`.

        Args:
            index: Cell index in [0..len-1].

        Raises:
            IndexError: if index is out of range.
        """
        ...

    # ---------- cell addressing by ID ----------

    async def resolve_cell_index(self, cell_id: str) -> int:
        """
        Return the zero-based index of the cell whose ``id`` matches *cell_id*.

        Allows callers to target cells by their stable identifiers rather than
        positional indices, which can shift as cells are inserted or deleted.

        The default implementation fetches the full notebook and performs a
        linear scan.  Transports backed by an indexed data structure (e.g. Yjs
        CRDT) MAY override this with a more efficient lookup.

        Args:
            cell_id: The unique cell identifier string (typically a UUID hex).

        Returns:
            Zero-based index of the matching cell.

        Raises:
            KeyError: if no cell with the given ID exists.
        """
        nb = await self.fetch()
        cells = nb.get("cells") or []
        for idx, cell in enumerate(cells):
            if cell.get("id") == cell_id:
                return idx
        raise KeyError(f"No cell with id {cell_id!r}")

    async def get_cell_by_id(self, cell_id: str) -> dict[str, Any]:
        """
        Return the cell whose ``id`` matches *cell_id* as an nbformat-like dict.

        This is a convenience method equivalent to::

            index = await self.resolve_cell_index(cell_id)
            cell  = await self.get_cell(index)

        Transports MAY provide a more efficient implementation.

        Args:
            cell_id: The unique cell identifier string.

        Returns:
            Dict with at least ``cell_type``, ``source``, ``metadata``, and,
            for code cells, ``outputs`` and ``execution_count``.

        Raises:
            KeyError: if no cell with the given ID exists.
        """
        index = await self.resolve_cell_index(cell_id)
        return await self.get_cell(index)

    # ---------- cell reordering ----------

    async def move_cell(self, from_index: int, to_index: int) -> None:
        """
        Move the cell at *from_index* to *to_index*.

        The cell is removed from its current position and re-inserted so that
        it occupies *to_index* in the resulting list.  All other cells shift
        accordingly.

        Implementations that operate on a CRDT (e.g. Yjs) SHOULD perform
        the move inside a single transaction so collaborators see an atomic
        reorder instead of a delete followed by an insert.

        Args:
            from_index: Current zero-based position of the cell to move.
            to_index:   Desired zero-based position after the move.

        Raises:
            IndexError: if either index is out of range ``[0..len-1]``.
        """
        ...

    # ---------- notebook-level metadata ----------

    async def get_metadata(self) -> dict[str, Any]:
        """
        Return the notebook-level metadata dict.

        This is the ``metadata`` key at the top level of the nbformat model,
        **not** per-cell metadata.  Typical contents include ``kernelspec``,
        ``language_info``, and custom keys set by tools or extensions.

        Returns:
            A (shallow-copied) dict of the notebook metadata.
        """
        nb = await self.fetch()
        return dict(nb.get("metadata") or {})

    async def update_metadata(self, updates: dict[str, Any]) -> None:
        """
        Merge *updates* into the notebook-level metadata.

        Semantics are a **shallow merge**: top-level keys in *updates*
        overwrite existing keys; keys not mentioned are left untouched.
        To delete a key, set it to ``None`` and have the implementation
        strip ``None``-valued keys (or simply overwrite).

        Args:
            updates: Mapping of keys to merge into ``notebook["metadata"]``.

        Raises:
            RuntimeError: on IO/network errors.
            TypeError: if *updates* is not a dict.
        """
        ...

    def on_change(self, cb: Callable[[dict[str, Any]], None]) -> None:
        """
        Register a callback to be invoked after a save or cell mutation.

        Implementations MAY no-op if they cannot produce change events.

        Callback signature:
            cb(event: Dict[str, Any]) -> None

        Event examples (non-normative; implementations may add fields):
            {"op": "save"}
            {"op": "cells-mutated", "index": 3}
            {"op": "cells-mutated", "kind": "insert_code", "index": 1}
            {"op": "y-update"}  # for collaborative transports
        """
        ...
