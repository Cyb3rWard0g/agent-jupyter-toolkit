"""Notebook tools for the MCP Jupyter server.

Registers all tools on a ``FastMCP`` instance via :func:`register_notebook_tools`.
Each tool is annotated with :class:`~mcp.types.ToolAnnotations` so that MCP
clients can make informed decisions about tool behaviour (read-only vs.
destructive, idempotent, open-world, etc.).

Tools access the shared :class:`~mcp_jupyter_notebook.context.AppContext`
(and therefore the ``NotebookSession``) through the MCP lifespan context.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from agent_jupyter_toolkit.kernel.variables import VariableManager
from agent_jupyter_toolkit.utils import (
    check_package_availability,
    ensure_packages_with_report,
    execute_code,
    get_session_info,
    get_variable_value,
    get_variables,
    invoke_code_cell,
    invoke_existing_cell,
    invoke_markdown_cell,
    invoke_notebook_cells,
)

log = logging.getLogger("mcp-jupyter.tools")


# ─────────────── helpers ───────────────


def _get_manager(ctx: Context):
    """Extract SessionManager from MCP lifespan context."""
    return ctx.request_context.lifespan_context.manager


def _get_session(ctx: Context, notebook_path: str | None = None):
    """Resolve a NotebookSession from the optional *notebook_path*.

    When *notebook_path* is ``None`` the default notebook is used,
    preserving full backward compatibility with single-notebook setups.
    """
    return _get_manager(ctx).get(notebook_path)


def _code_result(result: Any) -> dict[str, Any]:
    """Normalise a NotebookCodeExecutionResult to a dict."""
    return {
        "ok": result.status == "ok",
        "cell_index": result.cell_index,
        "execution_count": result.execution_count,
        "status": result.status,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "outputs": result.outputs,
        "text_outputs": result.text_outputs,
        "formatted_output": result.formatted_output,
        "error_message": result.error_message,
        "elapsed_seconds": result.elapsed_seconds,
    }


# ═══════════════ tool registration ═══════════════


def register_notebook_tools(mcp: FastMCP) -> None:  # noqa: C901
    """Register all notebook tools on the given ``FastMCP`` server.

    Every tool is decorated with :class:`ToolAnnotations` hints so that
    MCP clients can reason about side-effects before invocation.

    Parameters
    ----------
    mcp : FastMCP
        The server instance to attach tools to.
    """

    # ── notebook lifecycle ─────────────────────────────

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
        """Open a notebook and create a session for it.

        If the notebook is already open the existing session is returned.
        Pass ``set_default=True`` to make this notebook the default target
        for tools that omit ``notebook_path``.
        """
        manager = _get_manager(ctx)
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
        except Exception as e:
            return {"ok": False, "error": str(e)}

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
        """Close a notebook session and release its resources.

        The notebook's kernel is shut down and document transport
        disconnected.  If the closed notebook was the default, the
        default shifts to another open notebook (or clears).
        """
        manager = _get_manager(ctx)
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
        """List all currently open notebook sessions.

        Returns the path and default status of each open notebook.
        Use this to discover which notebooks are available before
        targeting a specific one with ``notebook_path``.
        """
        manager = _get_manager(ctx)
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
        """List ``.ipynb`` files available for opening.

        In **local** mode this scans the filesystem starting from
        *directory*.  In **server** mode it queries the Jupyter Contents
        API.  Each result includes an ``is_open`` flag indicating
        whether a session already exists for that notebook.

        Use this to discover notebooks before calling ``notebook_open``.
        """
        manager = _get_manager(ctx)
        try:
            files = await manager.list_notebook_files(directory=directory, recursive=recursive)
            return {
                "ok": True,
                "directory": directory,
                "recursive": recursive,
                "notebooks": files,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── code execution ───────────────────────────────

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
        """Append a new code cell to the notebook, execute it, and return outputs.

        The cell is persisted in the notebook document and its outputs
        (stdout, stderr, rich displays) are returned.  Use this as the
        primary way to run Python code in the Jupyter kernel.
        """
        session = _get_session(ctx, notebook_path)
        await ctx.info(f"Executing code cell ({len(code)} chars)")
        result = await invoke_code_cell(session, code, timeout=timeout)
        return _code_result(result)

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
        """Replace the source of an existing cell and re-execute it.

        Overwrites the cell's content in-place (0-based *cell_index*) and
        runs it.  Useful for correcting or updating a cell without appending
        a new one.
        """
        session = _get_session(ctx, notebook_path)
        await ctx.info(f"Re-executing cell {cell_index}")
        result = await invoke_existing_cell(session, cell_index, code, timeout=timeout)
        return _code_result(result)

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
        """Execute code directly in the kernel without creating a notebook cell.

        The code runs in the same kernel but does **not** appear in the
        notebook document.  Ideal for introspection, setup, or helper
        operations that should remain invisible to the end user.
        """
        session = _get_session(ctx, notebook_path)
        result = await execute_code(session.kernel, code, timeout=timeout)
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

    # ── multi-cell execution ─────────────────────────

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
        """Execute multiple cells sequentially in the notebook.

        Each element should be a mapping with ``type`` (``'code'`` or
        ``'markdown'``) and ``content`` (the cell source).  Code cells are
        executed; markdown cells are appended without execution.  Returns
        one result dict per cell.
        """
        session = _get_session(ctx, notebook_path)
        await ctx.info(f"Running {len(cells)} cells")
        results = await invoke_notebook_cells(session, cells, timeout=timeout)
        out = []
        for r in results:
            entry: dict[str, Any] = {
                "status": r.status,
                "cell_index": r.cell_index,
                "ok": r.status == "ok",
                "error_message": r.error_message,
                "elapsed_seconds": r.elapsed_seconds,
            }
            if hasattr(r, "stdout"):
                entry.update(
                    {
                        "execution_count": r.execution_count,
                        "stdout": r.stdout,
                        "stderr": r.stderr,
                        "outputs": r.outputs,
                        "text_outputs": r.text_outputs,
                        "formatted_output": r.formatted_output,
                    }
                )
            out.append(entry)
        return out

    # ── markdown ─────────────────────────────────────

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
        """Add a markdown cell to the notebook.

        The markdown is rendered in the notebook UI.  Use this for adding
        documentation, section headers, explanations, or narrative text
        between code cells.

        If *position* is given (0-based cell index), the cell is inserted
        at that position; otherwise it is appended to the end.
        """
        session = _get_session(ctx, notebook_path)
        result = await invoke_markdown_cell(session, content, index=position)
        return {
            "ok": result.status == "ok",
            "cell_index": result.cell_index,
            "error_message": result.error_message,
            "elapsed_seconds": result.elapsed_seconds,
        }

    # ── notebook document ────────────────────────────

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
        """Read the full notebook content.

        Returns an nbformat-compatible dict with every cell's source,
        type, execution count, and output count.  Use this to understand
        the current state of the notebook before making changes.
        """
        session = _get_session(ctx, notebook_path)
        nb = await session.doc.fetch()
        # Summarise cells for the agent
        cells_summary = []
        for i, cell in enumerate(nb.get("cells", [])):
            cells_summary.append(
                {
                    "index": i,
                    "cell_type": cell.get("cell_type"),
                    "source": cell.get("source", ""),
                    "execution_count": cell.get("execution_count"),
                    "outputs_count": len(cell.get("outputs", [])),
                }
            )
        return {
            "ok": True,
            "cell_count": len(cells_summary),
            "cells": cells_summary,
            "metadata": nb.get("metadata", {}),
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
        """Read a single cell from the notebook by its 0-based index.

        Returns the cell's type, source, metadata, and (for code cells)
        outputs and execution count.  Much cheaper than reading the
        entire notebook when you only need one cell — especially with
        collaborative (Yjs) transports where it avoids serialising the
        full document.
        """
        session = _get_session(ctx, notebook_path)
        try:
            cell = await session.get_cell(cell_index)
            return {"ok": True, "cell_index": cell_index, "cell": cell}
        except IndexError as e:
            return {"ok": False, "error": str(e)}

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
        """Read only the source text of a cell by its 0-based index.

        Returns just the source string without outputs or metadata.
        The lightest way to check what code or markdown is in a cell.
        """
        session = _get_session(ctx, notebook_path)
        try:
            source = await session.get_cell_source(cell_index)
            return {"ok": True, "cell_index": cell_index, "source": source}
        except IndexError as e:
            return {"ok": False, "error": str(e)}

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
        """Return the number of cells currently in the notebook.

        A lightweight check that avoids fetching the full notebook
        content.  Useful for determining valid cell index ranges before
        reading or modifying specific cells.
        """
        session = _get_session(ctx, notebook_path)
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
        """Delete a cell from the notebook by its 0-based index.

        Permanently removes the cell **and** its outputs from the
        notebook document.  This operation cannot be undone.
        """
        session = _get_session(ctx, notebook_path)
        await ctx.info(f"Deleting cell {cell_index}")
        try:
            await session.doc.delete_cell(cell_index)
            return {"ok": True, "deleted_index": cell_index}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── packages ─────────────────────────────────────

    @mcp.tool(
        title="Install Packages",
        annotations=ToolAnnotations(
            title="Install Packages",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    async def notebook_packages_install(
        packages: list[str],
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Install Python packages into the kernel environment.

        Accepts pip-style specifiers (e.g. ``'pandas>=2.0'``,
        ``'requests'``).  Packages that are already installed at a
        compatible version are skipped.
        """
        session = _get_session(ctx, notebook_path)
        await ctx.info(f"Installing packages: {', '.join(packages)}")
        report = await ensure_packages_with_report(session.kernel, packages)
        return {
            "ok": report.get("success", False),
            "packages": packages,
            "report": report.get("report", {}),
        }

    @mcp.tool(
        title="Check Package Availability",
        annotations=ToolAnnotations(
            title="Check Package Availability",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_packages_check(
        packages: list[str],
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Check which packages are available in the kernel without installing.

        Returns a mapping of package name → availability (``true``/``false``).
        Useful for deciding what to install before committing to an install
        operation.
        """
        session = _get_session(ctx, notebook_path)
        availability = await check_package_availability(session.kernel, packages)
        return {"ok": True, "packages": availability}

    # ── kernel control ───────────────────────────────

    @mcp.tool(
        title="Interrupt Kernel",
        annotations=ToolAnnotations(
            title="Interrupt Kernel",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_kernel_interrupt(
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Interrupt the running kernel execution.

        Sends a ``SIGINT`` to the kernel, which stops the currently
        running computation.  Useful for breaking out of long-running or
        stuck cells without restarting the whole kernel.
        """
        session = _get_session(ctx, notebook_path)
        await ctx.info("Interrupting kernel")
        try:
            await session.kernel.interrupt()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @mcp.tool(
        title="Kernel Info",
        annotations=ToolAnnotations(
            title="Kernel Info",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_kernel_info(
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Get detailed kernel metadata.

        Returns the Jupyter messaging protocol version, kernel
        implementation name and version, language info, and the startup
        banner.  Useful for determining the runtime environment.
        """
        session = _get_session(ctx, notebook_path)
        info = await session.kernel.kernel_info()
        return {
            "ok": info.status == "ok",
            "protocol_version": info.protocol_version,
            "implementation": info.implementation,
            "implementation_version": info.implementation_version,
            "language_info": info.language_info,
            "banner": info.banner,
        }

    @mcp.tool(
        title="Session Info",
        annotations=ToolAnnotations(
            title="Session Info",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_session_info(
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Get information about the current notebook session.

        Returns kernel type, whether it is alive, connection details,
        and the kernel name.  Useful for health-checking the connection.
        """
        session = _get_session(ctx, notebook_path)
        return await get_session_info(session.kernel)

    # ── kernel introspection ─────────────────────────

    @mcp.tool(
        title="Inspect Object",
        annotations=ToolAnnotations(
            title="Inspect Object",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_inspect(
        code: str,
        cursor_pos: int,
        ctx: Context,
        notebook_path: str | None = None,
        detail_level: int = 0,
    ) -> dict[str, Any]:
        """Inspect an object in the kernel at the given cursor position.

        Returns documentation, type information, and docstrings for the
        symbol under the cursor.  Set ``detail_level=1`` for more verbose
        output.  Useful for understanding APIs without executing code.
        """
        session = _get_session(ctx, notebook_path)
        result = await session.kernel.inspect(code, cursor_pos, detail_level=detail_level)
        return asdict(result)

    @mcp.tool(
        title="Code Completion",
        annotations=ToolAnnotations(
            title="Code Completion",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_complete(
        code: str,
        cursor_pos: int,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Get tab-completion suggestions for code at the cursor position.

        Returns a list of matching completions from the running kernel.
        Useful for discovering available methods, attributes, or variable
        names.
        """
        session = _get_session(ctx, notebook_path)
        result = await session.kernel.complete(code, cursor_pos)
        return asdict(result)

    @mcp.tool(
        title="Check Code Completeness",
        annotations=ToolAnnotations(
            title="Check Code Completeness",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_code_is_complete(
        code: str,
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Check whether a code fragment is syntactically complete.

        Asks the kernel to parse *code* and report whether it is
        ``'complete'``, ``'incomplete'`` (needs more input), or
        ``'invalid'``.  For incomplete code an indentation hint may be
        returned.  Useful for validating multi-line input before execution.
        """
        session = _get_session(ctx, notebook_path)
        result = await session.kernel.is_complete(code)
        return asdict(result)

    @mcp.tool(
        title="Execution History",
        annotations=ToolAnnotations(
            title="Execution History",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_kernel_history(
        ctx: Context,
        notebook_path: str | None = None,
        n: int = 10,
        output: bool = False,
        raw: bool = True,
    ) -> dict[str, Any]:
        """Retrieve recent execution history from the kernel.

        Returns the last *n* input entries (and optionally their outputs)
        from the kernel's history store.  Useful for understanding what
        code has been run previously in the session.
        """
        session = _get_session(ctx, notebook_path)
        result = await session.kernel.history(n=n, output=output, raw=raw)
        return {
            "ok": True,
            "entries": [asdict(e) for e in result.entries],
        }

    @mcp.tool(
        title="Restart Kernel",
        annotations=ToolAnnotations(
            title="Restart Kernel",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_kernel_restart(
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """Restart the Jupyter kernel, clearing all state.

        Shuts down the running kernel and starts a fresh one.  All
        variables, imports, and in-memory data are lost.  Use this as a
        last resort when the kernel is in a bad state.
        """
        session = _get_session(ctx, notebook_path)
        await ctx.info("Restarting kernel")
        try:
            await session.kernel.shutdown()
            await session.kernel.start()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── variables ────────────────────────────────────

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
        """List all user-defined variables in the kernel.

        Returns variable names visible in the kernel's global scope,
        excluding internal/private names that start with ``_``.
        """
        session = _get_session(ctx, notebook_path)
        variables = await get_variables(session.kernel)
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
        """Get the value of a specific variable from the kernel.

        Returns the JSON-serialisable value of the named variable, or
        ``null`` if it does not exist or cannot be serialised.
        """
        session = _get_session(ctx, notebook_path)
        value = await get_variable_value(session.kernel, name)
        return {"ok": value is not None, "name": name, "value": value}

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
        """Delete a notebook file and close its session if open.

        The notebook's kernel is shut down, its document transport
        disconnected, and the file is removed.  In **server** mode
        the file is deleted via the Jupyter Contents API.  In
        **local** mode it is removed from the filesystem.

        Returns ``{"ok": true}`` on success.  If the file was already
        absent the operation still succeeds (idempotent).
        """
        manager = _get_manager(ctx)
        await ctx.info(f"Deleting notebook: {notebook_path}")
        try:
            await manager.delete(notebook_path)
            return {
                "ok": True,
                "notebook_path": notebook_path,
                "default_notebook": manager.default_path,
                "open_notebooks": manager.paths,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

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
        """Set a variable in the kernel's global scope.

        Assigns the given JSON-serialisable *value* to *name* in the
        kernel, making it available to all subsequent code cells.
        """
        session = _get_session(ctx, notebook_path)
        await ctx.info(f"Setting variable '{name}'")
        try:
            vm = VariableManager(session.kernel)
            await vm.set(name, value)
            return {"ok": True, "name": name}
        except Exception as e:
            return {"ok": False, "name": name, "error": str(e)}
