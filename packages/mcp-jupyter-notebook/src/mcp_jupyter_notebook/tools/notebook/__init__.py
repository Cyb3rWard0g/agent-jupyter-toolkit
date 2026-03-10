"""Facade for the core notebook MCP tool pack.

The public entrypoint remains :func:`register_notebook_tools`, but the
implementation is split across private modules by concern so the notebook
tool pack can grow without concentrating every handler in one file.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

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

from .document import register_document_tools
from .execution import register_execution_tools
from .kernel import register_kernel_tools
from .lifecycle import register_lifecycle_tools
from .variables import register_variable_tools


def register_notebook_tools(mcp: FastMCP) -> None:
    """Register the built-in notebook tool pack on the given MCP server."""
    register_lifecycle_tools(mcp)
    register_execution_tools(
        mcp,
        execute_code_fn=execute_code,
        invoke_code_cell_fn=invoke_code_cell,
        invoke_existing_cell_fn=invoke_existing_cell,
        invoke_notebook_cells_fn=invoke_notebook_cells,
    )
    register_document_tools(
        mcp,
        invoke_markdown_cell_fn=invoke_markdown_cell,
    )
    register_kernel_tools(
        mcp,
        check_package_availability_fn=check_package_availability,
        ensure_packages_with_report_fn=ensure_packages_with_report,
        get_session_info_fn=get_session_info,
    )
    register_variable_tools(
        mcp,
        get_variable_value_fn=get_variable_value,
        get_variables_fn=get_variables,
        variable_manager_cls=VariableManager,
    )


__all__ = ["register_notebook_tools"]
