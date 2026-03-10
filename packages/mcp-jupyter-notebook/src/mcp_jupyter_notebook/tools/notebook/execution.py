"""Notebook execution MCP tools."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from .common import (
    cell_id_map_for_indices,
    enriched_code_result,
    get_session,
    run_all_result,
)


def register_execution_tools(
    mcp: FastMCP,
    *,
    execute_code_fn: Any,
    invoke_code_cell_fn: Any,
    invoke_existing_cell_fn: Any,
    invoke_notebook_cells_fn: Any,
) -> None:
    """Register notebook execution tools on the given MCP server."""

    @mcp.tool(
        title="Run Code Cell",
        annotations=ToolAnnotations(
            title="Run Code Cell",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    async def notebook_code_run(
        code: str,
        ctx: Context,
        notebook_path: str | None = None,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Append a new code cell to the notebook, execute it, and return outputs."""
        session = get_session(ctx, notebook_path)
        await ctx.info(f"Executing code cell ({len(code)} chars)")
        result = await invoke_code_cell_fn(session, code, timeout=timeout)
        return await enriched_code_result(session, result)

    @mcp.tool(
        title="Re-run Existing Cell",
        annotations=ToolAnnotations(
            title="Re-run Existing Cell",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    async def notebook_code_run_existing(
        cell_index: int,
        code: str,
        ctx: Context,
        notebook_path: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Replace the source of an existing cell and re-execute it."""
        session = get_session(ctx, notebook_path)
        await ctx.info(f"Re-executing cell {cell_index}")
        result = await invoke_existing_cell_fn(session, cell_index, code, timeout=timeout)
        return await enriched_code_result(session, result)

    @mcp.tool(
        title="Execute Code (Hidden)",
        annotations=ToolAnnotations(
            title="Execute Code (Hidden)",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    async def notebook_code_execute(
        code: str,
        ctx: Context,
        notebook_path: str | None = None,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Execute code directly in the kernel without creating a notebook cell."""
        session = get_session(ctx, notebook_path)
        result = await execute_code_fn(session.kernel, code, timeout=timeout)
        return {
            "ok": result.status == "ok",
            "status": result.status,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "outputs": result.outputs,
            "text_outputs": result.text_outputs,
            "formatted_output": result.formatted_output,
            "error_message": result.error_message,
            "elapsed_seconds": result.elapsed_seconds,
        }

    @mcp.tool(
        title="Run Multiple Cells",
        annotations=ToolAnnotations(
            title="Run Multiple Cells",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    async def notebook_cells_run(
        cells: list[dict[str, str]],
        ctx: Context,
        notebook_path: str | None = None,
        timeout: float = 120.0,
    ) -> list[dict[str, Any]]:
        """Execute multiple cells sequentially in the notebook."""
        session = get_session(ctx, notebook_path)
        await ctx.info(f"Running {len(cells)} cells")
        results = await invoke_notebook_cells_fn(session, cells, timeout=timeout)
        cell_id_map = await cell_id_map_for_indices(
            session,
            {
                result.cell_index
                for result in results
                if getattr(result, "cell_index", None) is not None and result.cell_index >= 0
            },
        )
        out = []
        for result in results:
            entry: dict[str, Any] = {
                "status": result.status,
                "cell_index": result.cell_index,
                "cell_id": cell_id_map.get(result.cell_index),
                "ok": result.status == "ok",
                "error_message": result.error_message,
                "elapsed_seconds": result.elapsed_seconds,
            }
            if hasattr(result, "stdout"):
                entry.update(
                    {
                        "execution_count": result.execution_count,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "outputs": result.outputs,
                        "text_outputs": result.text_outputs,
                        "formatted_output": result.formatted_output,
                    }
                )
            out.append(entry)
        return out

    @mcp.tool(
        title="Run All Code Cells",
        annotations=ToolAnnotations(
            title="Run All Code Cells",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    async def notebook_run_all(
        ctx: Context,
        notebook_path: str | None = None,
        timeout: float = 120.0,
        stop_on_error: bool = True,
    ) -> dict[str, Any]:
        """Execute every code cell in the notebook in document order."""
        session = get_session(ctx, notebook_path)
        await ctx.info("Running all notebook code cells")
        result = await session.run_all(stop_on_error=stop_on_error, timeout=timeout)
        return run_all_result(result)

    @mcp.tool(
        title="Restart And Run All Code Cells",
        annotations=ToolAnnotations(
            title="Restart And Run All Code Cells",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    async def notebook_restart_and_run_all(
        ctx: Context,
        notebook_path: str | None = None,
        timeout: float = 120.0,
        stop_on_error: bool = True,
    ) -> dict[str, Any]:
        """Restart the kernel, then execute every code cell from a clean state."""
        session = get_session(ctx, notebook_path)
        await ctx.info("Restarting kernel and running all notebook code cells")
        result = await session.restart_and_run_all(
            stop_on_error=stop_on_error,
            timeout=timeout,
        )
        return run_all_result(result)
