"""Actual kernel + actual Azure SDK pipeline; controlled transport, no Azure/socket access."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from agent_jupyter_toolkit.notebook import NotebookWorkspace, NotebookWorkspaceConfig
from mcp_jupyter_notebook._mcp import FastMCP
from mcp_jupyter_notebook.tools.azure_monitor import register_azure_monitor_tools

pytest.importorskip("agent_data_toolkit.azure_monitor", reason="Install the azure-monitor extra")
pytest.importorskip("azure.monitor.query", reason="Install the azure-monitor extra")

BOOTSTRAP = """
import json, time
from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import HttpTransport, HttpResponse
from azure.monitor.query import LogsQueryClient
from agent_data_toolkit.azure_monitor import (
    AzureMonitorLogsClient, ConnectionInfo, ConnectionManager,
)
from agent_data_toolkit.azure_monitor import connection as _connection

_requests = []
class Credential:
    def get_token(self, *scopes, **kwargs):
        return AccessToken("controlled-fixture-token", int(time.time()) + 3600)

class Response(HttpResponse):
    def __init__(self, request, value):
        super().__init__(request, None)
        self.status_code = 200
        self.headers = {"content-type": "application/json"}
        self.content_type = "application/json"
        self.value = json.dumps(value).encode()
    def body(self):
        return self.value
    def json(self):
        return json.loads(self.value)

class Transport(HttpTransport):
    def open(self): pass
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *exc): pass
    def send(self, request, **kwargs):
        body = json.loads(request.body)
        _requests.append(body)
        query = body["query"]
        if "fail" in query:
            raise RuntimeError("source-credential-canary")
        rows = [] if "empty" in query else [[query, 1]]
        value = {"tables": [{"name": "PrimaryResult", "columns": [
            {"name": "Query", "type": "string"}, {"name": "Count", "type": "long"}
        ], "rows": rows}]}
        if "partial" in query:
            value["error"] = {"code": "PartialError", "message": "Incomplete source result"}
        return Response(request, value)

_connection._make_client = lambda credential, info: LogsQueryClient(
    credential, endpoint=info.endpoint, audience=info.audience,
    transport=Transport(), retry_total=0
)
logs = AzureMonitorLogsClient.from_info(
    ConnectionInfo("22222222-2222-4222-8222-222222222222"), Credential()
)
"""


@pytest_asyncio.fixture
async def notebook(tmp_path):
    async with NotebookWorkspace(NotebookWorkspaceConfig(mode="local")) as workspace:
        path = str(tmp_path / "investigation.ipynb")
        session = await workspace.open(path)
        setup = await session.kernel.execute(BOOTSTRAP)
        assert setup.status == "ok", setup.stderr
        ctx = SimpleNamespace(
            request_context=SimpleNamespace(lifespan_context=SimpleNamespace(manager=workspace))
        )
        mcp = FastMCP("kernel-logs-test")
        register_azure_monitor_tools(mcp)
        yield SimpleNamespace(
            workspace=workspace,
            session=session,
            path=path,
            ctx=ctx,
            query=mcp._tool_manager._tools["azure_monitor_query_to_df"].fn,
            close=mcp._tool_manager._tools["azure_monitor_close"].fn,
            connect=mcp._tool_manager._tools["azure_monitor_connect"].fn,
        )


async def run_query(notebook, query="first", **kwargs):
    return await notebook.query(
        ctx=notebook.ctx,
        notebook_path=notebook.path,
        query=query,
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-02T00:00:00Z",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_query_runs_once_in_visible_cell_and_frame_survives(notebook):
    output = await run_query(notebook, df_name="events")
    assert output["ok"], output
    assert output["source"]["complete"]
    assert output["row_count"] == 1
    assert output["sample"] == [{"Query": "first", "Count": 1}]
    cell = await notebook.session.doc.get_cell(output["cell_index"])
    assert "logs.query(" in cell["source"]
    assert "events =" in cell["source"]
    assert "controlled-fixture-token" not in cell["source"]
    followup = await notebook.session.kernel.execute(
        "print(len(_requests), events.iloc[0]['Query'])"
    )
    assert followup.stdout.strip() == "1 first"
    saved = json.loads(Path(notebook.path).read_text())
    assert saved["cells"][output["cell_index"]]["outputs"]


@pytest.mark.parametrize(
    "query,allow_partial,expected_ok",
    [
        ("empty", False, True),
        ("partial", False, False),
        ("partial", True, True),
    ],
)
@pytest.mark.asyncio
async def test_empty_and_partial_source_status_is_preserved(
    notebook, query, allow_partial, expected_ok
):
    output = await run_query(notebook, query, allow_partial=allow_partial)
    assert output["ok"] is expected_ok, output
    assert output["source"]["complete"] == (query == "empty")
    assert output["dataframe_created"] == expected_ok
    followup = await notebook.session.kernel.execute("print(len(_requests))")
    assert followup.stdout.strip() == "1"


@pytest.mark.asyncio
async def test_concurrent_queries_reusing_dataframe_name_keep_exact_metadata(notebook):
    first, second = await asyncio.gather(
        run_query(notebook, "first", df_name="events"),
        run_query(notebook, "second", df_name="events"),
    )
    assert first["ok"] and second["ok"], (first, second)
    assert first["result_name"] != second["result_name"]
    assert first["sample"][0]["Query"] == "first"
    assert second["sample"][0]["Query"] == "second"
    followup = await notebook.session.kernel.execute("print(len(_requests))")
    assert followup.stdout.strip() == "2"


@pytest.mark.asyncio
async def test_failed_query_does_not_claim_previous_dataframe_or_repeat(notebook):
    assert (await run_query(notebook, "first", df_name="events"))["ok"]
    output = await run_query(notebook, "fail", df_name="events")
    assert output["ok"] is False
    assert output["dataframe_created"] is False
    assert "source" not in output
    assert "source-credential-canary" not in str(output)
    followup = await notebook.session.kernel.execute(
        "print(len(_requests), events.iloc[0]['Query'])"
    )
    assert followup.stdout.strip() == "2 first"


@pytest.mark.parametrize("operation", ["connect", "query", "close"])
@pytest.mark.asyncio
async def test_unrelated_kernel_object_is_not_used_closed_or_replaced(notebook, operation):
    setup = await notebook.session.kernel.execute(
        "class Unrelated:\n"
        "    def query(self, *args, **kwargs): raise AssertionError('unrelated query called')\n"
        "    def close(self): raise AssertionError('unrelated close called')\n"
        "unrelated = Unrelated()\n"
    )
    assert setup.status == "ok"
    if operation == "query":
        output = await run_query(notebook, connection_name="unrelated")
    else:
        output = await getattr(notebook, operation)(
            ctx=notebook.ctx, notebook_path=notebook.path, connection_name="unrelated"
        )
    assert not output["ok"]
    assert "different type" in output["error_message"]
    followup = await notebook.session.kernel.execute(
        "print(type(unrelated).__name__, len(_requests))"
    )
    assert followup.stdout.strip() == "Unrelated 0"


@pytest.mark.asyncio
async def test_metadata_failure_preserves_execution_identity_without_requery(notebook, monkeypatch):
    from unittest.mock import AsyncMock

    from agent_jupyter_toolkit.notebook.types import NotebookCodeExecutionResult

    monkeypatch.setattr(
        "mcp_jupyter_notebook.tools.azure_monitor.execute_code",
        AsyncMock(
            return_value=NotebookCodeExecutionResult(status="error", stderr="metadata failed")
        ),
    )
    output = await run_query(notebook, df_name="events")
    assert output["ok"] is False
    assert output["status"] == "ok"
    assert output["persistence_status"] == "ok"
    assert output["cell_id"]
    assert not output["metadata_extracted"]
    assert output["metadata_execution"]["status"] == "error"
    followup = await notebook.session.kernel.execute(
        "print(len(_requests), events.iloc[0]['Query'])"
    )
    assert followup.stdout.strip() == "1 first"


@pytest.mark.asyncio
async def test_output_persistence_failure_does_not_erase_successful_source_execution(
    notebook, monkeypatch
):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        notebook.session.doc,
        "update_cell_outputs_by_id",
        AsyncMock(side_effect=OSError("controlled output persistence failure")),
    )
    output = await run_query(notebook, df_name="events")
    assert output["ok"] is False
    assert output["status"] == "ok"
    assert output["persistence_status"] == "error"
    assert output["source"]["complete"]
    assert output["dataframe_created"] is True
    followup = await notebook.session.kernel.execute("print(len(_requests))")
    assert followup.stdout.strip() == "1"
