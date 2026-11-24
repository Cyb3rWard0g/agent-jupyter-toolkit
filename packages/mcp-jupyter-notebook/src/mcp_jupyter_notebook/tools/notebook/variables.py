"""Notebook variable MCP tools."""

from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations
from pydantic import JsonValue

from agent_jupyter_toolkit.actions import NotebookActions
from mcp_jupyter_notebook._mcp import Context, FastMCP

from .common import get_manager, shared_action


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
    @shared_action("variables_list")
    async def notebook_variables_list(
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        return await NotebookActions(
            get_manager(ctx), get_variables_fn=get_variables_fn
        ).variables_list(notebook_path=notebook_path)

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
    @shared_action("variable_get")
    async def notebook_variable_get(
        name: str,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        return await NotebookActions(
            get_manager(ctx), get_variable_value_fn=get_variable_value_fn
        ).variable_get(name=name, notebook_path=notebook_path)

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
    @shared_action("variable_set")
    async def notebook_variable_set(
        name: str,
        value: Any,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        await ctx.info(f"Setting variable '{name}'")
        return await NotebookActions(
            get_manager(ctx), variable_manager_cls=variable_manager_cls
        ).variable_set(name=name, value=value, notebook_path=notebook_path)

    @mcp.tool(
        title="Import Table",
        annotations=ToolAnnotations(
            title="Import Table",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    @shared_action("import_table")
    async def notebook_import_table(
        notebook_path: str,
        operation_id: str,
        name: str,
        columns: list[str],
        rows: list[list[JsonValue]],
        ctx: Context,
        metadata: dict[str, JsonValue] | None = None,
    ) -> dict[str, Any]:
        return await NotebookActions(get_manager(ctx)).import_table(
            notebook_path=notebook_path,
            operation_id=operation_id,
            name=name,
            columns=columns,
            rows=rows,
            metadata=metadata,
        )
