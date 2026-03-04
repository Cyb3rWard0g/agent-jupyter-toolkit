"""MCP Jupyter Notebook — an MCP server for AI agents to drive Jupyter notebooks.

This package exposes a single :func:`main` entry point that parses CLI
arguments, merges them with environment variables, and starts the MCP server
using the selected transport (``stdio``, ``sse``, or ``streamable-http``).

Typical usage::

    mcp-jupyter-notebook --mode server --base-url http://localhost:8888 --token <tok>

Or programmatically::

    from mcp_jupyter_notebook import main
    main()
"""

import argparse
import asyncio

from .context import AppContext


def main() -> None:
    """CLI entry point for ``mcp-jupyter-notebook``."""
    parser = argparse.ArgumentParser(
        description="MCP Jupyter Notebook server",
    )
    parser.add_argument(
        "--mode",
        default=None,
        help="Session mode: 'server' (remote Jupyter) or 'local' (default: server)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Jupyter server URL (required in server mode)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Jupyter API token",
    )
    parser.add_argument(
        "--kernel-name",
        default=None,
        help="Kernel name (default: python3)",
    )
    parser.add_argument(
        "--notebook-path",
        default=None,
        help="Notebook file path (default: auto-generated)",
    )
    parser.add_argument(
        "--transport",
        default=None,
        help="Transport: stdio (default), sse, streamable-http",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Host for HTTP transports (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for HTTP transports (default: 8000)",
    )

    parser.add_argument(
        "--enable-tools",
        action="append",
        default=None,
        help=(
            "Enable optional tool sets (repeatable or comma-separated). "
            "Example: --enable-tools postgresql"
        ),
    )

    args = parser.parse_args()

    from .server import process_config, run_server

    config = process_config(args)
    asyncio.run(run_server(config))


__all__ = ["AppContext", "main"]
