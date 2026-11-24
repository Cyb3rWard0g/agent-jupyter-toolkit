"""Opt-in MCP tools for saving and loading notebook-scoped Parquet datasets."""

from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations
from pydantic import JsonValue

from mcp_jupyter_notebook._mcp import Context, FastMCP

from .common import get_manager


def register_dataset_tools(mcp: FastMCP) -> None:
    """Expose the dataset tools on a server configured with shared storage."""

    annotations = ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )

    @mcp.tool(title="Begin Notebook Dataset", annotations=annotations)
    async def notebook_dataset_begin(
        notebook_path: str,
        operation_id: str,
        name: str,
        columns: list[str],
        row_count: int,
        ctx: Context,
        metadata: dict[str, JsonValue] | None = None,
    ) -> dict[str, Any]:
        """Begin a saved capture for an open notebook and return its ID and batch limits."""
        workspace = get_manager(ctx)
        if workspace.datasets is None:
            return {"ok": False, "error_code": "datasets_disabled"}
        return await workspace.datasets.begin(
            workspace.get(notebook_path),
            operation_id=operation_id,
            name=name,
            columns=columns,
            row_count=row_count,
            metadata=metadata,
        )

    @mcp.tool(title="Append Notebook Dataset Batch", annotations=annotations)
    async def notebook_dataset_append(
        notebook_path: str,
        dataset_id: str,
        index: int,
        rows: list[list[str | None]],
        ctx: Context,
    ) -> dict[str, Any]:
        """Append an ordered batch of string or null values. Identical retries succeed."""
        workspace = get_manager(ctx)
        if workspace.datasets is None:
            return {"ok": False, "error_code": "datasets_disabled"}
        return await workspace.datasets.append(
            workspace.get(notebook_path), dataset_id=dataset_id, index=index, rows=rows
        )

    @mcp.tool(title="Commit Notebook Dataset", annotations=annotations)
    async def notebook_dataset_commit(
        notebook_path: str, dataset_id: str, batch_count: int, ctx: Context
    ) -> dict[str, Any]:
        """Publish a complete capture and bind the table through a saved loader cell."""
        workspace = get_manager(ctx)
        if workspace.datasets is None:
            return {"ok": False, "error_code": "datasets_disabled"}
        return await workspace.datasets.commit(
            workspace.get(notebook_path), dataset_id=dataset_id, batch_count=batch_count
        )
