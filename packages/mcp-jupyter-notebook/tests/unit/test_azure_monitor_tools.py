"""Tool validation and uncertain execution boundaries without Azure access."""

from unittest.mock import AsyncMock

import pytest

from agent_jupyter_toolkit.notebook.types import NotebookCodeExecutionResult
from mcp_jupyter_notebook._mcp import FastMCP
from mcp_jupyter_notebook.tools.azure_monitor import register_azure_monitor_tools


def tool(name="azure_monitor_query_to_df"):
    mcp = FastMCP("logs-test")
    register_azure_monitor_tools(mcp)
    return mcp._tool_manager._tools[name].fn


@pytest.mark.parametrize(
    "changes",
    [
        {"connection_name": "bad-name"},
        {"df_name": "class"},
        {"df_name": "_adt_private"},
        {"connection_name": "globals"},
        {"df_name": "print"},
        {"df_name": "len"},
        {"connection_name": "isinstance"},
        {"df_name": "ｌｏｇｓ"},
        {"connection_name": "K"},
        {"df_name": "logs"},
        {"start_time": "2026-01-01"},
        {"end_time": "2025-01-01T00:00:00Z"},
        {"max_rows": 0},
        {"preview_rows": 21},
        {"timeout": float("nan")},
        {"server_timeout": 601},
        {"query": " "},
        {"table": -1},
    ],
)
@pytest.mark.asyncio
async def test_invalid_input_never_executes_kernel(mock_ctx, monkeypatch, changes):
    invoke = AsyncMock()
    monkeypatch.setattr("mcp_jupyter_notebook.tools.azure_monitor.invoke_code_cell", invoke)
    values = {
        "ctx": mock_ctx,
        "query": "SecurityEvent",
        "start_time": "2026-01-01T00:00:00Z",
        "end_time": "2026-01-02T00:00:00Z",
        **changes,
    }
    with pytest.raises(ValueError):
        await tool()(**values)
    invoke.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_execution_returns_evidence_without_metadata_or_retry(mock_ctx, monkeypatch):
    from types import SimpleNamespace

    mock_ctx.request_context.lifespan_context.manager.get.return_value = SimpleNamespace()
    result = NotebookCodeExecutionResult(
        status="error",
        cell_index=3,
        outcome="unknown",
        timed_out=True,
        persistence_status="error",
        persistence_error="save interrupted",
        request_id="request-1",
        cell_id="cell-1",
        source_hash="source-hash",
        kernel_generation=4,
    )
    invoke = AsyncMock(return_value=result)
    execute = AsyncMock()
    monkeypatch.setattr("mcp_jupyter_notebook.tools.azure_monitor.invoke_code_cell", invoke)
    monkeypatch.setattr("mcp_jupyter_notebook.tools.azure_monitor.execute_code", execute)
    output = await tool()(
        ctx=mock_ctx,
        query="SecurityEvent",
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-02T00:00:00Z",
    )
    assert output["outcome"] == "unknown"
    assert output["request_id"] == "request-1"
    assert output["cell_id"] == "cell-1"
    assert output["persistence_status"] == "error"
    assert output["dataframe_created"] is None
    invoke.assert_awaited_once()
    execute.assert_not_called()


@pytest.mark.parametrize("enabled", [[], ["azure-monitor"], ["azure_monitor"]])
def test_optional_registration(monkeypatch, enabled):
    import mcp_jupyter_notebook.server as server

    monkeypatch.setattr(server, "_server_config", {"enabled_tools": enabled})
    names = {t.name for t in server.create_server()._tool_manager.list_tools()}
    assert ("azure_monitor_query_to_df" in names) == bool(enabled)
