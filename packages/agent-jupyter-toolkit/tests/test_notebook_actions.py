"""The optional action interface shares SDK semantics without an MCP runtime."""

import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from agent_jupyter_toolkit.actions import NotebookActions
from agent_jupyter_toolkit.notebook import NotebookWorkspace
from agent_jupyter_toolkit.notebook.types import NotebookCodeExecutionResult

NAMES = {
    "open",
    "close",
    "list",
    "code_run",
    "markdown_add",
    "read",
    "cell_read_by_id",
    "cell_source_by_id",
    "cell_source_set_by_id",
    "packages_check",
    "dependencies_list",
    "kernel_info",
    "session_info",
    "inspect",
    "complete",
    "code_is_complete",
    "variables_list",
    "variable_get",
    "variable_set",
    "import_table",
}


def test_action_catalog_is_explicit_and_returns_independent_schemas():
    definitions = {item.name: item for item in NotebookActions.definitions()}
    assert set(definitions) == NAMES
    assert definitions["code_run"].input_schema["required"] == ["code"]
    assert definitions["code_run"].input_schema["properties"]["timeout"]["default"] == 120.0
    assert definitions["code_run"].input_schema["additionalProperties"] is False
    definitions["code_run"].input_schema["properties"].clear()
    fresh = {item.name: item for item in NotebookActions.definitions()}
    assert "code" in fresh["code_run"].input_schema["properties"]


def test_action_import_and_catalog_do_not_import_mcp():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "import sys",
                    "from agent_jupyter_toolkit.actions import NotebookActions",
                    "assert len(NotebookActions.definitions()) == 20",
                    "assert not any(n == 'mcp' or n.startswith('mcp.') for n in sys.modules)",
                    "assert not any(n.startswith('mcp_jupyter_notebook') for n in sys.modules)",
                ]
            ),
        ],
        check=True,
        timeout=30,
        capture_output=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,arguments",
    [
        ("delete", {}),
        ("__getattribute__", {}),
        ("code_run", {"code": None}),
        ("code_run", {"code": "pass", "timeout": True}),
        ("code_run", {"code": "pass", "timeout": float("nan")}),
        ("code_run", {"code": "pass", "timeout": float("inf")}),
        ("code_run", {"code": "pass", "unreviewed": 1}),
        ("markdown_add", {"content": "text", "position": "1"}),
        ("packages_check", {"packages": "pandas"}),
        ("inspect", {"code": "value", "cursor_pos": False}),
    ],
)
async def test_invalid_action_inputs_fail_before_session_access(name, arguments):
    workspace = MagicMock()
    actions = NotebookActions(workspace)
    with pytest.raises((ValueError, ValidationError)):
        await actions.invoke(name, arguments)
    workspace.get.assert_not_called()


@pytest.mark.asyncio
async def test_execution_result_retains_uncertainty_and_save_failure_without_retry():
    workspace = MagicMock()
    result = NotebookCodeExecutionResult(
        status="error",
        cell_index=2,
        cell_id="stable-cell",
        request_id="request-1",
        source_hash="a" * 64,
        kernel_generation=4,
        outcome="unknown",
        timed_out=True,
        persistence_status="error",
        persistence_error="save failed",
        output_truncated=True,
        dropped_output_bytes=40,
    )
    execute = AsyncMock(return_value=result)
    actions = NotebookActions(workspace, invoke_code_cell_fn=execute)
    actual = await actions.invoke("code_run", {"code": "value += 1"})
    assert actual["cell_id"] == "stable-cell"
    assert actual["request_id"] == "request-1"
    assert actual["outcome"] == "unknown"
    assert actual["timed_out"] is True
    assert actual["persistence_status"] == "error"
    assert actual["persistence_error"] == "save failed"
    assert actual["source_hash"] == "a" * 64
    assert actual["kernel_generation"] == 4
    assert actual["dropped_output_bytes"] == 40
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_kernel_reuses_state_and_preserves_cell_ids(tmp_path):
    async with NotebookWorkspace() as workspace:
        actions = NotebookActions(workspace)
        await actions.invoke("open", {"notebook_path": str(tmp_path / "actions.ipynb")})
        first = await actions.invoke("code_run", {"code": "answer = 21"})
        second = await actions.invoke("code_run", {"code": "print(answer * 2)"})
        assert first["status"] == second["status"] == "ok"
        assert second["stdout"] == "42\n"
        assert second["persistence_status"] == "ok"
        assert first["cell_id"] != second["cell_id"]
        cell = await actions.invoke("cell_read_by_id", {"cell_id": second["cell_id"]})
        assert cell["cell"]["source"] == "print(answer * 2)"
        assert (await actions.invoke("variable_get", {"name": "answer"}))["value"] == 21


@pytest.mark.asyncio
async def test_table_import_replays_and_rejects_conflicts(tmp_path):
    import asyncio

    pytest.importorskip("pandas")
    path = str(tmp_path / "imports.ipynb")
    marker = "fixture with quotes ' and newline\n"
    async with NotebookWorkspace() as workspace:
        actions = NotebookActions(workspace)
        await actions.open(path)
        args = {
            "notebook_path": path,
            "operation_id": "operation-1",
            "name": "alerts",
            "columns": ["event", "number"],
            "rows": [[marker, 2]],
            "metadata": {"query_row_limit": 10},
        }
        first, retry = await asyncio.gather(
            actions.invoke("import_table", args),
            actions.invoke("import_table", args),
        )
        assert (
            first
            == retry
            == {
                "ok": True,
                "table": "alerts",
                "operation_id": "operation-1",
                "row_count": 1,
                "column_count": 2,
            }
        )
        assert (await actions.invoke("import_table", {**args, "rows": [[None, 3]]}))[
            "error_code"
        ] == "operation_conflict"
        assert (await actions.invoke("import_table", {**args, "operation_id": "different"}))[
            "error_code"
        ] == "name_conflict"
        assert (await actions.invoke("import_table", {**args, "rows": [[None]]}))[
            "error_code"
        ] == "invalid_table_shape"
        result = await workspace.get(path).kernel.execute(
            "print(repr(alerts.iloc[0, 0]), alerts.attrs['notebook_import']['query_row_limit'])"
        )
        assert result.stdout.strip() == f"{marker!r} 10"
        await workspace.get(path).kernel.execute("del alerts")
        assert (await actions.invoke("import_table", args))["error_code"] == "table_unavailable"
        assert marker not in (tmp_path / "imports.ipynb").read_text()
