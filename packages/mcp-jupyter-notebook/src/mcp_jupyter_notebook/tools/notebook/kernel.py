"""Kernel and runtime MCP tools for notebook sessions."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from .common import get_session


def register_kernel_tools(
    mcp: FastMCP,
    *,
    check_package_availability_fn: Any,
    ensure_packages_with_report_fn: Any,
    get_session_info_fn: Any,
) -> None:
    """Register kernel, package, and introspection tools."""

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
        track: bool = True,
    ) -> dict[str, Any]:
        """Install Python packages into the kernel environment.

        When track=True (default), installed packages and their resolved versions
        are recorded in the notebook metadata under 'agent_dependencies', making
        the notebook self-documenting and reproducible.
        """
        session = get_session(ctx, notebook_path)
        await ctx.info(f"Installing packages: {', '.join(packages)}")
        report = await session.install_packages(packages, track=track)
        return {
            "ok": report.get("success", False),
            "packages": packages,
            "report": report.get("report", {}),
            "tracked": report.get("tracked", []),
        }

    @mcp.tool(
        title="Uninstall Packages",
        annotations=ToolAnnotations(
            title="Uninstall Packages",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    async def notebook_packages_uninstall(
        packages: list[str],
        ctx: Context,
        notebook_path: str | None = None,
        untrack: bool = True,
    ) -> dict[str, Any]:
        """Uninstall Python packages from the kernel environment.

        When untrack=True (default), the packages are also removed from the
        notebook's dependency manifest in metadata.
        """
        session = get_session(ctx, notebook_path)
        await ctx.info(f"Uninstalling packages: {', '.join(packages)}")
        report = await session.uninstall_packages(packages, untrack=untrack)
        return {
            "ok": report.get("success", False),
            "packages": packages,
            "report": report.get("report", {}),
            "untracked": report.get("untracked", []),
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
        """Check which packages are available in the kernel without installing."""
        session = get_session(ctx, notebook_path)
        availability = await check_package_availability_fn(session.kernel, packages)
        return {"ok": True, "packages": availability}

    @mcp.tool(
        title="List Tracked Dependencies",
        annotations=ToolAnnotations(
            title="List Tracked Dependencies",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def notebook_dependencies_list(
        ctx: Context,
        notebook_path: str | None = None,
    ) -> dict[str, Any]:
        """List packages that have been installed and tracked in the notebook metadata.

        Returns the dependency manifest stored under notebook.metadata.agent_dependencies,
        showing each package's version and when it was installed.
        """
        session = get_session(ctx, notebook_path)
        deps = await session.get_tracked_dependencies()
        return {
            "ok": True,
            "dependencies": deps,
            "count": len(deps),
        }

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
        """Interrupt the running kernel execution."""
        session = get_session(ctx, notebook_path)
        await ctx.info("Interrupting kernel")
        try:
            await session.kernel.interrupt()
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

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
        """Get detailed kernel metadata."""
        session = get_session(ctx, notebook_path)
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
        """Get information about the current notebook session."""
        session = get_session(ctx, notebook_path)
        return await get_session_info_fn(session.kernel)

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
        """Inspect an object in the kernel at the given cursor position."""
        session = get_session(ctx, notebook_path)
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
        """Get tab-completion suggestions for code at the cursor position."""
        session = get_session(ctx, notebook_path)
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
        """Check whether a code fragment is syntactically complete."""
        session = get_session(ctx, notebook_path)
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
        """Retrieve recent execution history from the kernel."""
        session = get_session(ctx, notebook_path)
        result = await session.kernel.history(n=n, output=output, raw=raw)
        return {
            "ok": result.status == "ok",
            "entries": [asdict(entry) for entry in result.history],
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
        """Restart the Jupyter kernel, clearing all state."""
        session = get_session(ctx, notebook_path)
        await ctx.info("Restarting kernel")
        try:
            await session.kernel.restart()
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
