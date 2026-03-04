"""
Unit tests for notebook tools using pytest and unittest.mock.

Since tools are now registered via @mcp.tool() decorators on an FastMCP
instance, we test the tool functions directly by importing them and calling
with a mock Context.
"""

from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP

from agent_jupyter_toolkit.notebook.types import (
    NotebookCodeExecutionResult,
    NotebookMarkdownCellResult,
)
from mcp_jupyter_notebook.tools import register_notebook_tools


@pytest.fixture
def mcp_server():
    """Create a real FastMCP and register notebook tools."""
    server = FastMCP("test-server")
    register_notebook_tools(server)
    return server


def test_register_notebook_tools_registers_expected_tools(mcp_server):
    """Test that all expected tools are registered on the server."""
    # FastMCP stores tools internally; check they exist
    tool_names = {t.name for t in mcp_server._tool_manager.list_tools()}
    expected = {
        "notebook_open",
        "notebook_close",
        "notebook_list",
        "notebook_files_list",
        "notebook_code_run",
        "notebook_code_run_existing",
        "notebook_code_execute",
        "notebook_cells_run",
        "notebook_markdown_add",
        "notebook_read",
        "notebook_cell_read",
        "notebook_cell_source",
        "notebook_cell_count",
        "notebook_cell_delete",
        "notebook_packages_install",
        "notebook_packages_check",
        "notebook_kernel_interrupt",
        "notebook_kernel_info",
        "notebook_session_info",
        "notebook_inspect",
        "notebook_complete",
        "notebook_code_is_complete",
        "notebook_kernel_history",
        "notebook_kernel_restart",
        "notebook_variables_list",
        "notebook_variable_get",
        "notebook_variable_set",
    }
    assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"


@pytest.mark.asyncio
async def test_notebook_markdown_add(monkeypatch, mock_ctx):
    """Test notebook_markdown_add tool returns correct result."""
    mock_result = NotebookMarkdownCellResult(
        status="ok",
        cell_index=42,
        error_message=None,
        elapsed_seconds=0.01,
    )
    monkeypatch.setattr(
        "mcp_jupyter_notebook.tools.notebook.invoke_markdown_cell",
        AsyncMock(return_value=mock_result),
    )

    server = FastMCP("test")
    register_notebook_tools(server)
    tool = server._tool_manager._tools["notebook_markdown_add"]
    result = await tool.fn(content="# Hello", ctx=mock_ctx)
    assert result["ok"] is True
    assert result["cell_index"] == 42


@pytest.mark.asyncio
async def test_notebook_code_run(monkeypatch, mock_ctx):
    """Test notebook_code_run tool returns expected result structure."""
    mock_result = NotebookCodeExecutionResult(
        status="ok",
        execution_count=1,
        cell_index=5,
        stdout="hi\n",
        stderr="",
        outputs=[],
        text_outputs=["hi"],
        formatted_output="hi",
        error_message=None,
        elapsed_seconds=0.02,
    )
    monkeypatch.setattr(
        "mcp_jupyter_notebook.tools.notebook.invoke_code_cell",
        AsyncMock(return_value=mock_result),
    )

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_code_run"]
    result = await tool.fn(code="print('hi')", ctx=mock_ctx)
    assert result["ok"] is True
    assert result["cell_index"] == 5
    assert result["stdout"] == "hi\n"


@pytest.mark.asyncio
async def test_notebook_packages_install(monkeypatch, mock_ctx):
    """Test notebook_packages_install tool calls ensure_packages_with_report."""
    mock_report = {
        "success": True,
        "report": {
            "pandas": {
                "pip": "pandas",
                "already": True,
                "installed": False,
                "success": True,
                "error": None,
                "pip_returncode": None,
                "pip_stderr": "",
            },
            "numpy": {
                "pip": "numpy",
                "already": True,
                "installed": False,
                "success": True,
                "error": None,
                "pip_returncode": None,
                "pip_stderr": "",
            },
        },
    }
    monkeypatch.setattr(
        "mcp_jupyter_notebook.tools.notebook.ensure_packages_with_report",
        AsyncMock(return_value=mock_report),
    )

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_packages_install"]
    result = await tool.fn(packages=["pandas", "numpy"], ctx=mock_ctx)
    assert result["ok"] is True
    assert result["packages"] == ["pandas", "numpy"]
    assert "report" in result


@pytest.mark.asyncio
async def test_notebook_read(mock_ctx):
    """Test notebook_read returns cell summaries from the document."""
    mock_nb = {
        "cells": [
            {
                "cell_type": "code",
                "source": "x = 1",
                "execution_count": 1,
                "outputs": [{"text": "1"}],
            },
            {"cell_type": "markdown", "source": "# Title", "outputs": []},
        ],
        "metadata": {"kernelspec": {"name": "python3"}},
    }
    mock_ctx.request_context.lifespan_context.session.doc.fetch = AsyncMock(return_value=mock_nb)

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_read"]
    result = await tool.fn(ctx=mock_ctx)
    assert result["ok"] is True
    assert result["cell_count"] == 2
    assert result["cells"][0]["cell_type"] == "code"
    assert result["cells"][1]["source"] == "# Title"


@pytest.mark.asyncio
async def test_notebook_cell_delete(mock_ctx):
    """Test notebook_cell_delete calls doc.delete_cell."""
    mock_ctx.request_context.lifespan_context.session.doc.delete_cell = AsyncMock()

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_cell_delete"]
    result = await tool.fn(cell_index=2, ctx=mock_ctx)
    assert result["ok"] is True
    assert result["deleted_index"] == 2
    mock_ctx.request_context.lifespan_context.session.doc.delete_cell.assert_awaited_once_with(2)


@pytest.mark.asyncio
async def test_notebook_cell_read(mock_ctx):
    """Test notebook_cell_read returns a single cell."""
    mock_cell = {
        "cell_type": "code",
        "source": "x = 1",
        "metadata": {},
        "outputs": [{"text": "1"}],
        "execution_count": 1,
    }
    mock_ctx.request_context.lifespan_context.session.get_cell = AsyncMock(return_value=mock_cell)

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_cell_read"]
    result = await tool.fn(cell_index=0, ctx=mock_ctx)
    assert result["ok"] is True
    assert result["cell"]["source"] == "x = 1"
    assert result["cell"]["cell_type"] == "code"
    mock_ctx.request_context.lifespan_context.session.get_cell.assert_awaited_once_with(0)


@pytest.mark.asyncio
async def test_notebook_cell_read_out_of_range(mock_ctx):
    """Test notebook_cell_read returns error for out-of-range index."""
    mock_ctx.request_context.lifespan_context.session.get_cell = AsyncMock(
        side_effect=IndexError("get_cell: index 99 out of range 0..1")
    )

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_cell_read"]
    result = await tool.fn(cell_index=99, ctx=mock_ctx)
    assert result["ok"] is False
    assert "out of range" in result["error"]


@pytest.mark.asyncio
async def test_notebook_cell_source(mock_ctx):
    """Test notebook_cell_source returns only the source text."""
    mock_ctx.request_context.lifespan_context.session.get_cell_source = AsyncMock(
        return_value="import pandas as pd"
    )

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_cell_source"]
    result = await tool.fn(cell_index=0, ctx=mock_ctx)
    assert result["ok"] is True
    assert result["source"] == "import pandas as pd"


@pytest.mark.asyncio
async def test_notebook_cell_count(mock_ctx):
    """Test notebook_cell_count returns the cell count."""
    mock_ctx.request_context.lifespan_context.session.cell_count = AsyncMock(return_value=5)

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_cell_count"]
    result = await tool.fn(ctx=mock_ctx)
    assert result["ok"] is True
    assert result["cell_count"] == 5


@pytest.mark.asyncio
async def test_notebook_packages_check(monkeypatch, mock_ctx):
    """Test notebook_packages_check returns availability info."""
    monkeypatch.setattr(
        "mcp_jupyter_notebook.tools.notebook.check_package_availability",
        AsyncMock(return_value={"pandas": True, "nonexistent": False}),
    )

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_packages_check"]
    result = await tool.fn(packages=["pandas", "nonexistent"], ctx=mock_ctx)
    assert result["ok"] is True
    assert result["packages"]["pandas"] is True
    assert result["packages"]["nonexistent"] is False


@pytest.mark.asyncio
async def test_notebook_kernel_interrupt(mock_ctx):
    """Test notebook_kernel_interrupt calls kernel.interrupt()."""
    mock_ctx.request_context.lifespan_context.session.kernel.interrupt = AsyncMock()

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_kernel_interrupt"]
    result = await tool.fn(ctx=mock_ctx)
    assert result["ok"] is True
    mock_ctx.request_context.lifespan_context.session.kernel.interrupt.assert_awaited_once()


@pytest.mark.asyncio
async def test_notebook_variable_set(mock_ctx):
    """Test notebook_variable_set uses VariableManager."""
    from unittest.mock import MagicMock, patch

    mock_vm = MagicMock()
    mock_vm.set = AsyncMock()

    with patch("mcp_jupyter_notebook.tools.notebook.VariableManager", return_value=mock_vm):
        server = FastMCP("test")
        register_notebook_tools(server)

        tool = server._tool_manager._tools["notebook_variable_set"]
        result = await tool.fn(name="x", value=42, ctx=mock_ctx)

    assert result["ok"] is True
    assert result["name"] == "x"
    mock_vm.set.assert_awaited_once_with("x", 42)


# ── lifecycle tools ──────────────────────────────


@pytest.mark.asyncio
async def test_notebook_open(mock_ctx):
    """Test notebook_open opens a session via the manager."""
    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_open"]
    result = await tool.fn(notebook_path="analysis.ipynb", ctx=mock_ctx)

    assert result["ok"] is True
    assert result["notebook_path"] == "analysis.ipynb"
    mock_ctx.request_context.lifespan_context.manager.open.assert_awaited_once_with(
        "analysis.ipynb"
    )


@pytest.mark.asyncio
async def test_notebook_close(mock_ctx):
    """Test notebook_close closes a session via the manager."""
    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_close"]
    result = await tool.fn(notebook_path="analysis.ipynb", ctx=mock_ctx)

    assert result["ok"] is True
    assert result["notebook_path"] == "analysis.ipynb"
    mock_ctx.request_context.lifespan_context.manager.close.assert_awaited_once_with(
        "analysis.ipynb"
    )


@pytest.mark.asyncio
async def test_notebook_list(mock_ctx):
    """Test notebook_list returns all open notebooks."""
    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_list"]
    result = await tool.fn(ctx=mock_ctx)

    assert result["ok"] is True
    assert result["default_notebook"] == "test.ipynb"
    assert len(result["notebooks"]) == 1
    assert result["notebooks"][0]["is_default"] is True


@pytest.mark.asyncio
async def test_notebook_files_list(mock_ctx):
    """Test notebook_files_list delegates to manager.list_notebook_files."""
    mock_files = [
        {"path": "analysis.ipynb", "name": "analysis.ipynb", "is_open": False},
        {"path": "data/clean.ipynb", "name": "clean.ipynb", "is_open": True},
    ]
    mock_ctx.request_context.lifespan_context.manager.list_notebook_files = AsyncMock(
        return_value=mock_files
    )

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_files_list"]
    result = await tool.fn(ctx=mock_ctx, directory=".", recursive=True)

    assert result["ok"] is True
    assert len(result["notebooks"]) == 2
    assert result["notebooks"][0]["name"] == "analysis.ipynb"
    assert result["recursive"] is True
    mock_ctx.request_context.lifespan_context.manager.list_notebook_files.assert_awaited_once_with(
        directory=".", recursive=True
    )
