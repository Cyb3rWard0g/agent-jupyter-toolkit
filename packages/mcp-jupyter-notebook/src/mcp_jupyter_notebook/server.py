"""MCP Jupyter Notebook server.

Uses the MCP Python SDK ``FastMCP`` with a lifespan context that manages
the :class:`~agent_jupyter_toolkit.notebook.NotebookSession`.  Configuration
is assembled from CLI flags and environment variables (CLI wins), then the
session mode (*server* or *local*) determines how the kernel and notebook
document transports are wired together.

See Also
--------
mcp_jupyter_notebook.tools : Tool registrations using ``ToolAnnotations``.
mcp_jupyter_notebook.context : The ``AppContext`` dataclass shared via lifespan.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_jupyter_notebook.context import AppContext, SessionManager
from mcp_jupyter_notebook.tools import register_notebook_tools

# ───────────────────────── logging ─────────────────────────


def _init_logging() -> logging.Logger:
    level = getattr(
        logging,
        os.getenv("MCP_JUPYTER_LOG_LEVEL", "INFO").upper(),
        logging.INFO,
    )
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return logging.getLogger("mcp-jupyter")


log = _init_logging()


# ───────────────── config helpers ─────────────────


def _parse_headers_env(var: str = "MCP_JUPYTER_HEADERS_JSON") -> dict[str, str]:
    """Parse optional extra headers from a JSON environment variable.

    Useful for injecting ``Cookie`` or ``X-XSRFToken`` headers when the
    Jupyter server is behind an authenticating proxy.

    Parameters
    ----------
    var : str
        Name of the environment variable containing a JSON object of
        ``{header: value}`` pairs.

    Returns
    -------
    dict[str, str]
        Parsed headers, or an empty dict if the variable is unset/invalid.
    """
    raw = os.getenv(var)
    if not raw:
        return {}
    try:
        h = json.loads(raw)
        if not isinstance(h, dict):
            raise ValueError("headers JSON must be an object")
        return {str(k): str(v) for k, v in h.items()}
    except Exception as e:
        log.warning("Ignoring invalid %s: %s", var, e)
        return {}


def process_config(args: Any) -> dict[str, Any]:
    """Merge CLI arguments with environment variables into a flat config dict.

    CLI flags always take precedence over environment variables.  Sensible
    defaults are applied where neither source provides a value.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed command-line arguments (may have ``None`` for unset flags).

    Returns
    -------
    dict[str, Any]
        Configuration dictionary consumed by :func:`_build_session` and
        :func:`run_server`.
    """
    cfg: dict[str, Any] = {}

    # Session mode
    cfg["mode"] = (
        args.mode if args.mode is not None else os.getenv("MCP_JUPYTER_SESSION_MODE", "server")
    ).lower()

    # Base URL
    cfg["base_url"] = (
        args.base_url if args.base_url is not None else os.getenv("MCP_JUPYTER_BASE_URL")
    )

    # Token
    cfg["token"] = args.token if args.token is not None else os.getenv("MCP_JUPYTER_TOKEN")

    # Kernel name
    cfg["kernel_name"] = (
        args.kernel_name
        if args.kernel_name is not None
        else os.getenv("MCP_JUPYTER_KERNEL_NAME", "python3")
    )

    # Notebook path
    cfg["notebook_path"] = (
        args.notebook_path
        if args.notebook_path is not None
        else os.getenv("MCP_JUPYTER_NOTEBOOK_PATH")
    ) or f"mcp_{uuid.uuid4().hex[:8]}.ipynb"

    # Transport
    cfg["transport"] = (
        args.transport
        if args.transport is not None
        else os.getenv("MCP_JUPYTER_TRANSPORT", "stdio")
    ).lower()

    # HTTP options
    cfg["host"] = args.host if args.host is not None else os.getenv("MCP_JUPYTER_HOST", "127.0.0.1")
    cfg["port"] = int(args.port if args.port is not None else os.getenv("MCP_JUPYTER_PORT", "8000"))

    # Headers
    cfg["headers"] = _parse_headers_env()

    # Collab transport (real-time Yjs sync in browser)
    collab_env = os.getenv("MCP_JUPYTER_PREFER_COLLAB", "true").lower()
    cfg["prefer_collab"] = collab_env in ("1", "true", "yes")

    # Optional tool sets
    # - CLI: --enable-tools postgresql --enable-tools other
    # - ENV: MCP_JUPYTER_ENABLE_TOOLS=postgresql,other
    enabled: list[str] = []
    if getattr(args, "enable_tools", None):
        raw = args.enable_tools
        if isinstance(raw, str):
            enabled = [t.strip().lower() for t in raw.split(",") if t.strip()]
        else:
            # argparse append list
            enabled = []
            for item in raw:
                enabled.extend([t.strip().lower() for t in str(item).split(",") if t.strip()])
    else:
        env_raw = os.getenv("MCP_JUPYTER_ENABLE_TOOLS", "")
        enabled = [t.strip().lower() for t in env_raw.split(",") if t.strip()]

    cfg["enabled_tools"] = sorted(set(enabled))

    return cfg


# ──────────────── lifespan & server factory ────────────────

# Module-global config; set by main() before the server runs.
_server_config: dict[str, Any] = {}


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """MCP lifespan handler — manage the ``SessionManager`` lifecycle.

    Creates a :class:`SessionManager`, opens the default notebook (from
    ``--notebook-path`` / ``MCP_JUPYTER_NOTEBOOK_PATH``), and tears down
    all sessions on shutdown.

    Yields
    ------
    AppContext
        Shared context made available to all tool handlers via
        ``ctx.request_context.lifespan_context``.
    """
    cfg = _server_config
    default_path = cfg.get("notebook_path")

    manager = SessionManager(config=cfg, default_path=None)

    # Auto-open the default notebook so single-notebook usage works unchanged
    if default_path:
        log.info("Opening default notebook: %s", default_path)
        await manager.open(default_path)
    else:
        log.info("No default notebook configured; agents must use notebook_open.")

    try:
        yield AppContext(manager=manager)
    finally:
        log.info("Shutting down SessionManager (%d sessions)…", len(manager))
        try:
            await manager.close_all()
            log.info("All sessions stopped.")
        except Exception as e:
            log.warning("Error during shutdown: %s", e)


def create_server() -> FastMCP:
    """Create the ``FastMCP`` server with lifespan and register notebook tools.

    Returns
    -------
    FastMCP
        A fully-configured MCP server instance ready to be started with
        one of the ``run_*_async`` methods.
    """
    mcp = FastMCP(
        "mcp-jupyter-notebook",
        instructions=(
            "MCP server for interacting with Jupyter notebooks. "
            "Provides tools to run code cells, add markdown, manage packages, "
            "inspect kernel variables, read notebook content, delete cells, "
            "interrupt execution, inspect objects, and get kernel info. "
            "Changes appear in real-time when using collab transport."
        ),
        lifespan=app_lifespan,
    )
    register_notebook_tools(mcp)

    # Optional tools are enabled via CLI/env and registered at startup.
    enabled_tools = _server_config.get("enabled_tools", [])
    if enabled_tools:
        log.info("Enabling optional tool sets: %s", ", ".join(enabled_tools))

    if "postgresql" in enabled_tools or "postgres" in enabled_tools:
        from mcp_jupyter_notebook.tools import register_postgresql_tools

        register_postgresql_tools(mcp)

    return mcp


# ───────────────────── async entry ─────────────────────


async def run_server(cfg: dict[str, Any]) -> None:
    """Async entry point — create and run the MCP server.

    Selects the appropriate transport (``stdio``, ``sse``, or
    ``streamable-http``) based on *cfg* and blocks until the server
    shuts down.

    Parameters
    ----------
    cfg : dict[str, Any]
        Configuration produced by :func:`process_config`.

    Raises
    ------
    SystemExit
        If an unknown transport is specified.
    """
    global _server_config
    _server_config = cfg

    mcp = create_server()
    transport = cfg["transport"]

    log.info(
        "mcp-jupyter-notebook starting: mode=%s kernel=%s nb=%s transport=%s",
        cfg["mode"],
        cfg["kernel_name"],
        cfg["notebook_path"],
        transport,
    )

    match transport:
        case "stdio":
            await mcp.run_stdio_async()
        case "sse":
            await mcp.run_sse_async(host=cfg["host"], port=cfg["port"])
        case "streamable-http":
            await mcp.run_streamable_http_async(
                host=cfg["host"],
                port=cfg["port"],
            )
        case _:
            raise SystemExit(f"Unknown transport: {transport}")


if __name__ == "__main__":
    from mcp_jupyter_notebook import main

    main()
