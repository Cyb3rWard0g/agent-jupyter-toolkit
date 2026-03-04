"""Tool registrations for the MCP Jupyter Notebook server.

This is a package so we can group tool sets by domain:
- Notebook tools live in :mod:`mcp_jupyter_notebook.tools.notebook`.
- Optional database helpers can live alongside (e.g. PostgreSQL).

Keeping :func:`register_notebook_tools` re-exported from this module
preserves backward compatibility for imports like
``from mcp_jupyter_notebook.tools import register_notebook_tools``.
"""

from __future__ import annotations

from .notebook import register_notebook_tools
from .postgresql import register_postgresql_tools

__all__ = [
    "register_notebook_tools",
    "register_postgresql_tools",
]
