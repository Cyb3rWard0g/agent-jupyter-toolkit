"""Notebook variable MCP tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from .common import get_session


def register_variable_tools(
    mcp: FastMCP,
    *,
    get_variable_value_fn: Any,
    get_variables_fn: Any,
    variable_manager_cls: type[Any],
) -> None:
    """Register variable inspection and mutation tools."""

    @mcp.tool(
        title="List Variables",
        annotations=ToolAnnotations(
            title="List Variables",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_variables_list(
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """List all user-defined variables in the kernel."""
        session = get_session(ctx, notebook_path)
        variables = await get_variables_fn(session.kernel)
        return {"ok": True, "variables": variables}

    @mcp.tool(
        title="Get Variable",
        annotations=ToolAnnotations(
            title="Get Variable",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_variable_get(
        name: str,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Get the value of a specific variable from the kernel."""
        session = get_session(ctx, notebook_path)
        value = await get_variable_value_fn(session.kernel, name)
        return {"ok": value is not None, "name": name, "value": value}

    @mcp.tool(
        title="Set Variable",
        annotations=ToolAnnotations(
            title="Set Variable",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_variable_set(
        name: str,
        value: Any,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Set a variable in the kernel's global scope."""
        session = get_session(ctx, notebook_path)
        await ctx.info(f"Setting variable '{name}'")
        try:
            variable_manager = variable_manager_cls(session.kernel)
            await variable_manager.set(name, value)
            return {"ok": True, "name": name}
        except Exception as exc:
            return {"ok": False, "name": name, "error": str(exc)}
