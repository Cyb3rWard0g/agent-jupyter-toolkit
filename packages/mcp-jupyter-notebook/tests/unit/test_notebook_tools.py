"""
Unit tests for notebook tools using pytest and unittest.mock.

Since tools are now registered via @mcp.tool() decorators on an FastMCP
instance, we test the tool functions directly by importing them and calling
with a mock Context.
"""

from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP

from agent_jupyter_toolkit.kernel.types import HistoryEntry, HistoryResult
from agent_jupyter_toolkit.notebook.types import (
    CellRunResult,
    NotebookCodeExecutionResult,
    NotebookMarkdownCellResult,
    RunAllResult,
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
        "notebook_delete",
        "notebook_list",
        "notebook_files_list",
        "notebook_code_run",
        "notebook_code_run_existing",
        "notebook_code_execute",
        "notebook_cells_run",
        "notebook_run_all",
        "notebook_restart_and_run_all",
        "notebook_markdown_add",
        "notebook_read",
        "notebook_cell_read",
        "notebook_cell_read_by_id",
        "notebook_cell_source",
        "notebook_cell_source_by_id",
        "notebook_cell_source_set_by_id",
        "notebook_cell_count",
        "notebook_cell_delete",
        "notebook_cell_delete_by_id",
        "notebook_cell_move",
        "notebook_cell_move_before",
        "notebook_cell_move_after",
        "notebook_packages_install",
        "notebook_packages_uninstall",
        "notebook_packages_check",
        "notebook_dependencies_list",
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
    assert result["cell_id"] == "cell-1"


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
    assert result["cell_id"] == "cell-1"
    assert result["stdout"] == "hi\n"


@pytest.mark.asyncio
async def test_notebook_cells_run_includes_cell_ids(monkeypatch, mock_ctx):
    """Test notebook_cells_run resolves stable IDs with one notebook fetch."""
    mock_results = [
        NotebookMarkdownCellResult(
            status="ok",
            cell_index=0,
            error_message=None,
            elapsed_seconds=0.01,
        ),
        NotebookCodeExecutionResult(
            status="ok",
            execution_count=1,
            cell_index=1,
            stdout="hi\n",
            stderr="",
            outputs=[],
            text_outputs=["hi"],
            formatted_output="hi",
            error_message=None,
            elapsed_seconds=0.02,
        ),
    ]
    mock_ctx.request_context.lifespan_context.session.doc.fetch = AsyncMock(
        return_value={
            "cells": [
                {"id": "cell-md", "cell_type": "markdown", "source": "# Header"},
                {"id": "cell-code", "cell_type": "code", "source": "print('hi')"},
            ]
        }
    )
    monkeypatch.setattr(
        "mcp_jupyter_notebook.tools.notebook.invoke_notebook_cells",
        AsyncMock(return_value=mock_results),
    )

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_cells_run"]
    result = await tool.fn(
        cells=[
            {"type": "markdown", "content": "# Header"},
            {"type": "code", "content": "print('hi')"},
        ],
        ctx=mock_ctx,
    )
    assert result[0]["cell_id"] == "cell-md"
    assert result[1]["cell_id"] == "cell-code"
    mock_ctx.request_context.lifespan_context.session.doc.fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_notebook_packages_install(monkeypatch, mock_ctx):
    """Test notebook_packages_install tool calls session.install_packages."""
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
        "tracked": ["pandas", "numpy"],
    }
    mock_ctx.request_context.lifespan_context.session.install_packages = AsyncMock(
        return_value=mock_report,
    )

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_packages_install"]
    result = await tool.fn(packages=["pandas", "numpy"], ctx=mock_ctx)
    assert result["ok"] is True
    assert result["packages"] == ["pandas", "numpy"]
    assert "report" in result
    assert result["tracked"] == ["pandas", "numpy"]


@pytest.mark.asyncio
async def test_notebook_read(mock_ctx):
    """Test notebook_read returns cell summaries from the document."""
    mock_nb = {
        "cells": [
            {
                "id": "cell-1",
                "cell_type": "code",
                "source": ["x = 1", "\nprint(x)"],
                "execution_count": 1,
                "outputs": [{"text": "1"}],
            },
            {"id": "cell-2", "cell_type": "markdown", "source": "# Title", "outputs": []},
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
    assert result["cells"][0]["cell_id"] == "cell-1"
    assert result["cells"][0]["cell_type"] == "code"
    assert result["cells"][0]["source"] == "x = 1\nprint(x)"
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
        "id": "cell-7",
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
    assert result["cell_id"] == "cell-7"
    assert result["cell"]["source"] == "x = 1"
    assert result["cell"]["cell_type"] == "code"
    mock_ctx.request_context.lifespan_context.session.get_cell.assert_awaited_once_with(0)


@pytest.mark.asyncio
async def test_notebook_cell_read_by_id(mock_ctx):
    """Test notebook_cell_read_by_id returns a single cell by stable ID."""
    mock_cell = {
        "id": "cell-9",
        "cell_type": "markdown",
        "source": "# Title",
        "metadata": {},
    }
    mock_ctx.request_context.lifespan_context.session.doc.get_cell_by_id = AsyncMock(
        return_value=mock_cell
    )

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_cell_read_by_id"]
    result = await tool.fn(cell_id="cell-9", ctx=mock_ctx)
    assert result["ok"] is True
    assert result["cell_id"] == "cell-9"
    assert result["cell"]["cell_type"] == "markdown"
    mock_ctx.request_context.lifespan_context.session.doc.get_cell_by_id.assert_awaited_once_with(
        "cell-9"
    )


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
async def test_notebook_cell_source_by_id(mock_ctx):
    """Test notebook_cell_source_by_id normalizes list-form source."""
    mock_ctx.request_context.lifespan_context.session.doc.get_cell_by_id = AsyncMock(
        return_value={"id": "cell-4", "cell_type": "code", "source": ["x = 1", "\nprint(x)"]}
    )

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_cell_source_by_id"]
    result = await tool.fn(cell_id="cell-4", ctx=mock_ctx)
    assert result["ok"] is True
    assert result["cell_id"] == "cell-4"
    assert result["source"] == "x = 1\nprint(x)"


@pytest.mark.asyncio
async def test_notebook_cell_source_set_by_id(mock_ctx):
    """Test notebook_cell_source_set_by_id resolves then updates the cell."""
    mock_ctx.request_context.lifespan_context.session.doc.resolve_cell_index = AsyncMock(
        return_value=6
    )
    mock_ctx.request_context.lifespan_context.session.doc.set_cell_source = AsyncMock()

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_cell_source_set_by_id"]
    result = await tool.fn(cell_id="cell-6", source="x = 42", ctx=mock_ctx)
    assert result["ok"] is True
    assert result["cell_index"] == 6
    assert result["source"] == "x = 42"
    mock_ctx.request_context.lifespan_context.session.doc.resolve_cell_index.assert_awaited_once_with(
        "cell-6"
    )
    mock_ctx.request_context.lifespan_context.session.doc.set_cell_source.assert_awaited_once_with(
        6, "x = 42"
    )


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
async def test_notebook_cell_delete_by_id(mock_ctx):
    """Test notebook_cell_delete_by_id resolves then deletes the cell."""
    mock_ctx.request_context.lifespan_context.session.doc.resolve_cell_index = AsyncMock(
        return_value=3
    )
    mock_ctx.request_context.lifespan_context.session.doc.delete_cell = AsyncMock()

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_cell_delete_by_id"]
    result = await tool.fn(cell_id="cell-3", ctx=mock_ctx)
    assert result["ok"] is True
    assert result["deleted_index"] == 3
    mock_ctx.request_context.lifespan_context.session.doc.resolve_cell_index.assert_awaited_once_with(
        "cell-3"
    )
    mock_ctx.request_context.lifespan_context.session.doc.delete_cell.assert_awaited_once_with(3)


@pytest.mark.asyncio
async def test_notebook_cell_move(mock_ctx):
    """Test notebook_cell_move uses stable IDs for the source cell."""
    mock_ctx.request_context.lifespan_context.session.doc.resolve_cell_index = AsyncMock(
        return_value=4
    )
    mock_ctx.request_context.lifespan_context.session.doc.move_cell = AsyncMock()

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_cell_move"]
    result = await tool.fn(cell_id="cell-4", to_index=1, ctx=mock_ctx)
    assert result["ok"] is True
    assert result["from_index"] == 4
    assert result["to_index"] == 1
    mock_ctx.request_context.lifespan_context.session.doc.resolve_cell_index.assert_awaited_once_with(
        "cell-4"
    )
    mock_ctx.request_context.lifespan_context.session.doc.move_cell.assert_awaited_once_with(4, 1)


@pytest.mark.asyncio
async def test_notebook_cell_move_before_adjusts_target_index(mock_ctx):
    """Test notebook_cell_move_before accounts for index shift after removal."""
    mock_ctx.request_context.lifespan_context.session.doc.resolve_cell_index = AsyncMock(
        side_effect=[1, 4]
    )
    mock_ctx.request_context.lifespan_context.session.doc.move_cell = AsyncMock()

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_cell_move_before"]
    result = await tool.fn(cell_id="cell-1", before_cell_id="cell-4", ctx=mock_ctx)
    assert result["ok"] is True
    assert result["from_index"] == 1
    assert result["to_index"] == 3
    mock_ctx.request_context.lifespan_context.session.doc.move_cell.assert_awaited_once_with(1, 3)


@pytest.mark.asyncio
async def test_notebook_cell_move_after_adjusts_target_index(mock_ctx):
    """Test notebook_cell_move_after accounts for source cells below the anchor."""
    mock_ctx.request_context.lifespan_context.session.doc.resolve_cell_index = AsyncMock(
        side_effect=[5, 2]
    )
    mock_ctx.request_context.lifespan_context.session.doc.move_cell = AsyncMock()

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_cell_move_after"]
    result = await tool.fn(cell_id="cell-5", after_cell_id="cell-2", ctx=mock_ctx)
    assert result["ok"] is True
    assert result["from_index"] == 5
    assert result["to_index"] == 3
    mock_ctx.request_context.lifespan_context.session.doc.move_cell.assert_awaited_once_with(5, 3)


@pytest.mark.asyncio
async def test_notebook_cell_move_before_same_cell_is_noop(mock_ctx):
    """Test move-before on the same cell ID avoids unnecessary document writes."""
    mock_ctx.request_context.lifespan_context.session.doc.resolve_cell_index = AsyncMock(
        side_effect=[2, 2]
    )
    mock_ctx.request_context.lifespan_context.session.doc.move_cell = AsyncMock()

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_cell_move_before"]
    result = await tool.fn(cell_id="cell-2", before_cell_id="cell-2", ctx=mock_ctx)
    assert result["ok"] is True
    assert result["from_index"] == 2
    assert result["to_index"] == 2
    mock_ctx.request_context.lifespan_context.session.doc.move_cell.assert_not_awaited()


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
async def test_notebook_run_all(mock_ctx):
    mock_ctx.request_context.lifespan_context.session.run_all = AsyncMock(
        return_value=RunAllResult(
            status="ok",
            executed_count=2,
            skipped_count=1,
            cells=[
                CellRunResult(index=0, status="skipped"),
                CellRunResult(index=1, status="ok", execution_count=1),
                CellRunResult(index=2, status="ok", execution_count=2),
            ],
        )
    )

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_run_all"]
    result = await tool.fn(ctx=mock_ctx, timeout=30.0, stop_on_error=True)
    assert result["ok"] is True
    assert result["executed_count"] == 2
    assert result["skipped_count"] == 1
    mock_ctx.request_context.lifespan_context.session.run_all.assert_awaited_once_with(
        stop_on_error=True,
        timeout=30.0,
    )


@pytest.mark.asyncio
async def test_notebook_restart_and_run_all(mock_ctx):
    mock_ctx.request_context.lifespan_context.session.restart_and_run_all = AsyncMock(
        return_value=RunAllResult(status="error", executed_count=1, skipped_count=0)
    )

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_restart_and_run_all"]
    result = await tool.fn(ctx=mock_ctx, timeout=45.0, stop_on_error=False)
    assert result["ok"] is False
    assert result["status"] == "error"
    mock_ctx.request_context.lifespan_context.session.restart_and_run_all.assert_awaited_once_with(
        stop_on_error=False,
        timeout=45.0,
    )


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
async def test_notebook_kernel_history(mock_ctx):
    mock_ctx.request_context.lifespan_context.session.kernel.history = AsyncMock(
        return_value=HistoryResult(
            history=[HistoryEntry(session=1, line_number=7, input="x = 1", output=None)],
            status="ok",
        )
    )

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_kernel_history"]
    result = await tool.fn(ctx=mock_ctx, n=5, output=False, raw=True)
    assert result["ok"] is True
    assert result["entries"][0]["input"] == "x = 1"


@pytest.mark.asyncio
async def test_notebook_kernel_restart_uses_restart_api(mock_ctx):
    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_kernel_restart"]
    result = await tool.fn(ctx=mock_ctx)
    assert result["ok"] is True
    mock_ctx.request_context.lifespan_context.session.kernel.restart.assert_awaited_once()


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


@pytest.mark.asyncio
async def test_notebook_packages_uninstall(mock_ctx):
    """Test notebook_packages_uninstall tool calls session.uninstall_packages."""
    mock_report = {
        "success": True,
        "report": {
            "pandas": {
                "pip": "pandas",
                "was_installed": True,
                "uninstalled": True,
                "error": None,
            },
        },
        "untracked": ["pandas"],
    }
    mock_ctx.request_context.lifespan_context.session.uninstall_packages = AsyncMock(
        return_value=mock_report,
    )

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_packages_uninstall"]
    result = await tool.fn(packages=["pandas"], ctx=mock_ctx)
    assert result["ok"] is True
    assert result["packages"] == ["pandas"]
    assert result["untracked"] == ["pandas"]
    mock_ctx.request_context.lifespan_context.session.uninstall_packages.assert_awaited_once()


@pytest.mark.asyncio
async def test_notebook_dependencies_list(mock_ctx):
    """Test notebook_dependencies_list returns tracked dependency manifest."""
    mock_deps = {
        "pandas": {"version": "2.1.0", "installed_at": "2024-01-15T00:00:00+00:00"},
        "numpy": {"version": "1.26.0", "installed_at": "2024-01-15T00:00:00+00:00"},
    }
    mock_ctx.request_context.lifespan_context.session.get_tracked_dependencies = AsyncMock(
        return_value=mock_deps,
    )

    server = FastMCP("test")
    register_notebook_tools(server)

    tool = server._tool_manager._tools["notebook_dependencies_list"]
    result = await tool.fn(ctx=mock_ctx)
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["dependencies"]["pandas"]["version"] == "2.1.0"
    assert result["dependencies"]["numpy"]["version"] == "1.26.0"
