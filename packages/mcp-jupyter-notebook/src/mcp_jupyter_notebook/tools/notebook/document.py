"""Notebook document and cell MCP tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from .common import (
    cell_id_for_index,
    get_session,
    move_after_index,
    move_before_index,
    normalize_source,
    summarize_cell,
)


def register_document_tools(
    mcp: FastMCP,
    *,
    invoke_markdown_cell_fn: Any,
) -> None:
    """Register notebook document and cell tools on the given MCP server."""

    @mcp.tool(
        title="Add Markdown Cell",
        annotations=ToolAnnotations(
            title="Add Markdown Cell",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    async def notebook_markdown_add(
        content: str,
        ctx: Context,
        notebook_path: str | None = None,
        position: int | None = None,
    ) -> dict[str, Any]:
        """Add a markdown cell to the notebook."""
        session = get_session(ctx, notebook_path)
        result = await invoke_markdown_cell_fn(session, content, index=position)
        return {
            "ok": result.status == "ok",
            "cell_index": result.cell_index,
            "cell_id": await cell_id_for_index(session, result.cell_index),
            "error_message": result.error_message,
            "elapsed_seconds": result.elapsed_seconds,
        }

    @mcp.tool(
        title="Read Notebook",
        annotations=ToolAnnotations(
            title="Read Notebook",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_read(
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Read the full notebook content."""
        session = get_session(ctx, notebook_path)
        notebook = await session.doc.fetch()
        cells_summary = [
            summarize_cell(cell, index) for index, cell in enumerate(notebook.get("cells", []))
        ]
        return {
            "ok": True,
            "cell_count": len(cells_summary),
            "cells": cells_summary,
            "metadata": notebook.get("metadata", {}),
        }

    @mcp.tool(
        title="Read Cell",
        annotations=ToolAnnotations(
            title="Read Cell",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_cell_read(
        cell_index: int,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Read a single cell from the notebook by its 0-based index."""
        session = get_session(ctx, notebook_path)
        try:
            cell = await session.get_cell(cell_index)
            return {
                "ok": True,
                "cell_index": cell_index,
                "cell_id": cell.get("id"),
                "cell": cell,
            }
        except IndexError as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool(
        title="Read Cell By ID",
        annotations=ToolAnnotations(
            title="Read Cell By ID",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_cell_read_by_id(
        cell_id: str,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Read a single cell using its stable Jupyter cell ID."""
        session = get_session(ctx, notebook_path)
        try:
            cell = await session.doc.get_cell_by_id(cell_id)
            return {"ok": True, "cell_id": cell_id, "cell": cell}
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool(
        title="Read Cell Source",
        annotations=ToolAnnotations(
            title="Read Cell Source",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_cell_source(
        cell_index: int,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Read only the source text of a cell by its 0-based index."""
        session = get_session(ctx, notebook_path)
        try:
            source = await session.get_cell_source(cell_index)
            return {"ok": True, "cell_index": cell_index, "source": source}
        except IndexError as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool(
        title="Read Cell Source By ID",
        annotations=ToolAnnotations(
            title="Read Cell Source By ID",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_cell_source_by_id(
        cell_id: str,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Read only the source text of a cell using its stable ID."""
        session = get_session(ctx, notebook_path)
        try:
            cell = await session.doc.get_cell_by_id(cell_id)
            return {
                "ok": True,
                "cell_id": cell_id,
                "source": normalize_source(cell.get("source", "")),
            }
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool(
        title="Set Cell Source By ID",
        annotations=ToolAnnotations(
            title="Set Cell Source By ID",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_cell_source_set_by_id(
        cell_id: str,
        source: str,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Replace a cell's source using its stable ID without executing it."""
        session = get_session(ctx, notebook_path)
        await ctx.info(f"Updating source for cell {cell_id}")
        try:
            cell_index = await session.doc.resolve_cell_index(cell_id)
            await session.doc.set_cell_source(cell_index, source)
            return {
                "ok": True,
                "cell_id": cell_id,
                "cell_index": cell_index,
                "source": source,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool(
        title="Cell Count",
        annotations=ToolAnnotations(
            title="Cell Count",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_cell_count(
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Return the number of cells currently in the notebook."""
        session = get_session(ctx, notebook_path)
        count = await session.cell_count()
        return {"ok": True, "cell_count": count}

    @mcp.tool(
        title="Delete Cell",
        annotations=ToolAnnotations(
            title="Delete Cell",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_cell_delete(
        cell_index: int,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Delete a cell from the notebook by its 0-based index."""
        session = get_session(ctx, notebook_path)
        await ctx.info(f"Deleting cell {cell_index}")
        try:
            await session.doc.delete_cell(cell_index)
            return {"ok": True, "deleted_index": cell_index}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool(
        title="Delete Cell By ID",
        annotations=ToolAnnotations(
            title="Delete Cell By ID",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_cell_delete_by_id(
        cell_id: str,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Delete a cell using its stable ID instead of a positional index."""
        session = get_session(ctx, notebook_path)
        await ctx.info(f"Deleting cell {cell_id}")
        try:
            cell_index = await session.doc.resolve_cell_index(cell_id)
            await session.doc.delete_cell(cell_index)
            return {"ok": True, "cell_id": cell_id, "deleted_index": cell_index}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool(
        title="Move Cell",
        annotations=ToolAnnotations(
            title="Move Cell",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_cell_move(
        cell_id: str,
        to_index: int,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Move a cell to a new position using its stable ID as the source handle."""
        session = get_session(ctx, notebook_path)
        await ctx.info(f"Moving cell {cell_id} to index {to_index}")
        try:
            from_index = await session.doc.resolve_cell_index(cell_id)
            await session.doc.move_cell(from_index, to_index)
            return {
                "ok": True,
                "cell_id": cell_id,
                "from_index": from_index,
                "to_index": to_index,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool(
        title="Move Cell Before",
        annotations=ToolAnnotations(
            title="Move Cell Before",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_cell_move_before(
        cell_id: str,
        before_cell_id: str,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Move a cell so it ends up immediately before another stable cell ID."""
        session = get_session(ctx, notebook_path)
        await ctx.info(f"Moving cell {cell_id} before {before_cell_id}")
        try:
            from_index = await session.doc.resolve_cell_index(cell_id)
            anchor_index = await session.doc.resolve_cell_index(before_cell_id)
            to_index = move_before_index(from_index, anchor_index)
            if from_index != to_index:
                await session.doc.move_cell(from_index, to_index)
            return {
                "ok": True,
                "cell_id": cell_id,
                "before_cell_id": before_cell_id,
                "from_index": from_index,
                "to_index": to_index,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool(
        title="Move Cell After",
        annotations=ToolAnnotations(
            title="Move Cell After",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_cell_move_after(
        cell_id: str,
        after_cell_id: str,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Move a cell so it ends up immediately after another stable cell ID."""
        session = get_session(ctx, notebook_path)
        await ctx.info(f"Moving cell {cell_id} after {after_cell_id}")
        try:
            from_index = await session.doc.resolve_cell_index(cell_id)
            anchor_index = await session.doc.resolve_cell_index(after_cell_id)
            to_index = move_after_index(from_index, anchor_index)
            if from_index != to_index:
                await session.doc.move_cell(from_index, to_index)
            return {
                "ok": True,
                "cell_id": cell_id,
                "after_cell_id": after_cell_id,
                "from_index": from_index,
                "to_index": to_index,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
