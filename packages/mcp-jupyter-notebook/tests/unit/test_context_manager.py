"""MCP configuration and tool routing through the core notebook workspace."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_jupyter_toolkit.notebook import NotebookWorkspace
from agent_jupyter_toolkit.notebook.types import NotebookCodeExecutionResult
from mcp_jupyter_notebook._mcp import FastMCP
from mcp_jupyter_notebook.context import AppContext, SessionManager
from mcp_jupyter_notebook.tools.notebook import register_notebook_tools


def test_missing_session_reports_mcp_recovery_tool():
    manager = SessionManager({"mode": "local"})
    with pytest.raises(ValueError, match="notebook_open"):
        manager.get()
    with pytest.raises(ValueError, match="notebook_open"):
        manager.get("missing.ipynb")


def test_mcp_configuration_builds_the_same_core_server_transports(monkeypatch):
    import agent_jupyter_toolkit.utils as utils

    create_kernel = MagicMock()
    create_document = MagicMock()
    monkeypatch.setattr(utils, "create_kernel", create_kernel)
    monkeypatch.setattr(utils, "create_notebook_transport", create_document)
    headers = {"X-Test": "original"}
    manager = SessionManager(
        {
            "mode": "server",
            "base_url": "https://jupyter.example.test",
            "token": "test-token",
            "headers": headers,
            "kernel_name": "custom-python",
            "prefer_collab": False,
            "collaboration_mode": "required",
            "transport": "streamable-http",
            "port": 8123,
        }
    )
    headers["X-Test"] = "changed after construction"
    session = manager._build_session("analysis.ipynb")
    assert isinstance(manager, NotebookWorkspace)
    create_kernel.assert_called_once_with(
        "remote",
        base_url="https://jupyter.example.test",
        token="test-token",
        headers={"X-Test": "original"},
        kernel_name="custom-python",
        notebook_path="analysis.ipynb",
    )
    create_document.assert_called_once_with(
        "remote",
        "analysis.ipynb",
        base_url="https://jupyter.example.test",
        token="test-token",
        headers={"X-Test": "original"},
        prefer_collab=False,
        collaboration_mode="required",
        create_if_missing=True,
    )
    assert session.kernel is create_kernel.return_value
    assert session.doc is create_document.return_value


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["local", "server"])
async def test_mcp_open_switch_run_and_close_use_core_default_without_ids(
    mode, tmp_path, monkeypatch
):
    import mcp_jupyter_notebook.tools.notebook as notebook_tools

    monkeypatch.chdir(tmp_path)
    manager = SessionManager({"mode": mode})
    monkeypatch.setattr(
        manager,
        "_build_session",
        lambda path: SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), doc=SimpleNamespace()),
    )
    invoke = AsyncMock(return_value=NotebookCodeExecutionResult(cell_id="cell-1"))
    monkeypatch.setattr(notebook_tools, "invoke_code_cell", invoke)
    server = FastMCP("workspace-routing-test")
    register_notebook_tools(server)
    tools = server._tool_manager._tools
    ctx = SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=AppContext(manager)), info=AsyncMock()
    )
    analysis_alias = "./analysis.ipynb" if mode == "local" else "/analysis.ipynb/"
    report_alias = "./report.ipynb" if mode == "local" else "/report.ipynb/"

    async with manager:
        first = await tools["notebook_open"].fn(notebook_path=analysis_alias, ctx=ctx)
        assert first["ok"] is True
        assert first["is_default"] is True
        assert first["notebook_path"] == analysis_alias
        analysis = manager.get()

        opened = await tools["notebook_open"].fn(notebook_path="report.ipynb", ctx=ctx)
        assert opened["ok"] is True
        assert opened["is_default"] is False
        report = manager.get("report.ipynb")

        await tools["notebook_code_run"].fn(code="x = 10", ctx=ctx)
        invoke.assert_awaited_with(analysis, "x = 10", timeout=120.0)
        await tools["notebook_code_run"].fn(code="x = 99", notebook_path=report_alias, ctx=ctx)
        invoke.assert_awaited_with(report, "x = 99", timeout=120.0)
        assert manager.get() is analysis

        switched = await tools["notebook_open"].fn(
            notebook_path=report_alias, set_default=True, ctx=ctx
        )
        assert switched["ok"] is True
        assert switched["is_default"] is True
        assert ctx.request_context.lifespan_context.session is report
        await tools["notebook_code_run"].fn(code="print(x)", ctx=ctx)
        invoke.assert_awaited_with(report, "print(x)", timeout=120.0)

        listed = await tools["notebook_list"].fn(ctx=ctx)
        assert listed["default_notebook"] == manager.default_path
        assert [entry["is_default"] for entry in listed["notebooks"]] == [False, True]
        analysis.stop.assert_not_awaited()
        report.start.assert_awaited_once()

        closed = await tools["notebook_close"].fn(notebook_path=report_alias, ctx=ctx)
        assert closed["ok"] is True
        assert manager.get() is analysis
        report.stop.assert_awaited_once()
        await tools["notebook_code_run"].fn(code="print(x)", ctx=ctx)
        invoke.assert_awaited_with(analysis, "print(x)", timeout=120.0)

    analysis.stop.assert_awaited_once()
