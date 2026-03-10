"""Notebook lifecycle MCP tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from .common import get_manager


def register_lifecycle_tools(mcp: FastMCP) -> None:
    """Register notebook lifecycle tools on the given MCP server."""

    @mcp.tool(
        title="Open Notebook",
        annotations=ToolAnnotations(
            title="Open Notebook",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    async def notebook_open(
        notebook_path: str,
        ctx: Context,
        set_default: bool = False,
    ) -> dict[str, Any]:
        """Open a notebook and create a session for it."""
        manager = get_manager(ctx)
        await ctx.info(f"Opening notebook: {notebook_path}")
        try:
            await manager.open(notebook_path)
            if set_default:
                manager.default_path = notebook_path
            return {
                "ok": True,
                "notebook_path": notebook_path,
                "is_default": notebook_path == manager.default_path,
                "open_notebooks": manager.paths,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool(
        title="Close Notebook",
        annotations=ToolAnnotations(
            title="Close Notebook",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_close(
        notebook_path: str,
        ctx: Context,
    ) -> dict[str, Any]:
        """Close a notebook session and release its resources."""
        manager = get_manager(ctx)
        await ctx.info(f"Closing notebook: {notebook_path}")
        closed = await manager.close(notebook_path)
        return {
            "ok": closed,
            "notebook_path": notebook_path,
            "default_notebook": manager.default_path,
            "open_notebooks": manager.paths,
        }

    @mcp.tool(
        title="List Open Notebooks",
        annotations=ToolAnnotations(
            title="List Open Notebooks",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_list(
        ctx: Context,
    ) -> dict[str, Any]:
        """List all currently open notebook sessions."""
        manager = get_manager(ctx)
        return {
            "ok": True,
            "default_notebook": manager.default_path,
            "notebooks": manager.list_sessions(),
        }

    @mcp.tool(
        title="List Notebook Files",
        annotations=ToolAnnotations(
            title="List Notebook Files",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    async def notebook_files_list(
        ctx: Context,
        directory: str = ".",
        recursive: bool = False,
    ) -> dict[str, Any]:
        """List available notebook files."""
        manager = get_manager(ctx)
        try:
            files = await manager.list_notebook_files(directory=directory, recursive=recursive)
            return {
                "ok": True,
                "directory": directory,
                "recursive": recursive,
                "notebooks": files,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool(
        title="Delete Notebook",
        annotations=ToolAnnotations(
            title="Delete Notebook",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_delete(
        notebook_path: str,
        ctx: Context,
    ) -> dict[str, Any]:
        """Delete a notebook file and close its session if open."""
        manager = get_manager(ctx)
        await ctx.info(f"Deleting notebook: {notebook_path}")
        try:
            await manager.delete(notebook_path)
            return {
                "ok": True,
                "notebook_path": notebook_path,
                "default_notebook": manager.default_path,
                "open_notebooks": manager.paths,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
