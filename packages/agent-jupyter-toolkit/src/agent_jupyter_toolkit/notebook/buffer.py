"""
In-memory notebook buffer for staged edits.

This module provides NotebookBuffer, a lightweight, list-like wrapper over a
NotebookDocumentTransport. It allows staged, multi-step edits in memory and a
single commit to the underlying storage.
"""

from __future__ import annotations

import uuid
from collections.abc import MutableSequence
from typing import Any

import nbformat

from .cells import create_code_cell, create_markdown_cell
from .transport import NotebookDocumentTransport


class NotebookBuffer(MutableSequence):
    """
    In-memory notebook buffer with explicit load/commit lifecycle.

    This class keeps a notebook model in memory for efficient, multi-step edits.
    Changes are only persisted when `commit()` is called.
    """

    def __init__(self, transport: NotebookDocumentTransport) -> None:
        self._transport = transport
        self._doc: dict[str, Any] | None = None
        self._dirty = False

    async def load(self) -> None:
        """Fetch the notebook from the transport into memory."""
        self._doc = await self._transport.fetch()
        self._dirty = False

    async def commit(self) -> None:
        """Persist the in-memory notebook to the transport."""
        self._ensure_loaded()
        if self._dirty:
            await self._transport.save(self._doc)
            self._dirty = False

    @property
    def dirty(self) -> bool:
        """True if the buffer has uncommitted changes."""
        return self._dirty

    @property
    def metadata(self) -> dict[str, Any]:
        """Notebook-level metadata."""
        self._ensure_loaded()
        return self._doc.setdefault("metadata", {})

    @metadata.setter
    def metadata(self, value: dict[str, Any]) -> None:
        self._ensure_loaded()
        self._doc["metadata"] = dict(value)
        self._dirty = True

    @property
    def nbformat(self) -> int:
        """Notebook format major version."""
        self._ensure_loaded()
        return int(self._doc.get("nbformat", 4))

    @property
    def nbformat_minor(self) -> int:
        """Notebook format minor version."""
        self._ensure_loaded()
        return int(self._doc.get("nbformat_minor", 5))

    def __len__(self) -> int:
        self._ensure_loaded()
        return len(self._cells)

    def __getitem__(self, index: int) -> dict[str, Any]:
        self._ensure_loaded()
        return self._cells[index]

    def __setitem__(self, index: int, value: dict[str, Any]) -> None:
        self._ensure_loaded()
        cell = self._normalize_cell(value)
        self._cells[index] = cell
        self._dirty = True

    def __delitem__(self, index: int) -> None:
        self._ensure_loaded()
        del self._cells[index]
        self._dirty = True

    def insert(self, index: int, value: dict[str, Any]) -> None:
        self._ensure_loaded()
        cell = self._normalize_cell(value)
        self._cells.insert(index, cell)
        self._dirty = True

    def append_code_cell(
        self,
        source: str,
        metadata: dict[str, Any] | None = None,
        outputs: list[dict[str, Any]] | None = None,
        execution_count: int | None = None,
    ) -> int:
        """Append a code cell and return its index."""
        self._ensure_loaded()
        cell = create_code_cell(
            source=source,
            metadata=metadata,
            outputs=outputs,
            execution_count=execution_count,
        )
        self._cells.append(self._normalize_cell(cell))
        self._dirty = True
        return len(self._cells) - 1

    def append_markdown_cell(self, source: str, metadata: dict[str, Any] | None = None) -> int:
        """Append a markdown cell and return its index."""
        self._ensure_loaded()
        cell = create_markdown_cell(source=source, metadata=metadata)
        self._cells.append(self._normalize_cell(cell))
        self._dirty = True
        return len(self._cells) - 1

    def set_cell_source(self, index: int, source: str) -> None:
        """Replace the source text for a cell."""
        self._ensure_loaded()
        self._cells[index]["source"] = source
        self._dirty = True

    def update_cell_outputs(
        self, index: int, outputs: list[dict[str, Any]], execution_count: int | None
    ) -> None:
        """Replace outputs and execution count for a code cell."""
        self._ensure_loaded()
        cell = self._cells[index]
        if cell.get("cell_type") != "code":
            raise TypeError("update_cell_outputs requires a code cell")
        cell["outputs"] = list(outputs or [])
        cell["execution_count"] = execution_count
        self._dirty = True

    @property
    def _cells(self) -> list[dict[str, Any]]:
        return self._doc.setdefault("cells", [])

    def _ensure_loaded(self) -> None:
        if self._doc is None:
            raise RuntimeError("NotebookBuffer is not loaded. Call load() first.")

    def _normalize_cell(self, cell: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(cell, dict):
            cell = nbformat.from_dict(cell)
        if "id" not in cell:
            cell["id"] = uuid.uuid4().hex
        return cell
