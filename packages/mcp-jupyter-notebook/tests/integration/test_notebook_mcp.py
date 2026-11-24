"""Exercise the MCP entry point with real local and shared Jupyter kernels."""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
import zmq
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_jupyter_toolkit.notebook import NotebookWorkspace, NotebookWorkspaceConfig

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def _connect_mcp(cwd, *args, env=None):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_jupyter_notebook", *args],
        cwd=cwd,
        env=env,
    )
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as client:
        async with asyncio.timeout(30):
            await client.initialize()
        yield client


async def _call(client, tool, **arguments):
    async with asyncio.timeout(50):
        result = await client.call_tool(tool, arguments)
    response = result.model_dump(by_alias=True)
    assert not response.get("isError"), result.content
    assert response["structuredContent"] is not None
    return response["structuredContent"]


@pytest.mark.skipif(not zmq.has("curve"), reason="libzmq has no Curve support")
async def test_local_mcp_keeps_required_encryption_after_restart(tmp_path):
    kernelspec = pytest.importorskip("ipykernel.kernelspec")
    spec_dir = tmp_path / "kernels" / "test-python"
    spec_dir.mkdir(parents=True)
    (spec_dir / "kernel.json").write_text(
        json.dumps(kernelspec.get_kernel_dict()), encoding="utf-8"
    )

    async with _connect_mcp(
        tmp_path,
        "--mode",
        "local",
        "--kernel-name",
        "test-python",
        "--notebook-path",
        "encrypted.ipynb",
        "--transport-encryption",
        "required",
        env={"JUPYTER_PATH": str(tmp_path), "JUPYTER_RUNTIME_DIR": str(tmp_path / "runtime")},
    ) as client:
        before = await _call(client, "notebook_session_info")
        assert before["alive"] and before["encryption_enabled"]
        assert before["transport_encryption"] == "required"
        assert (await _call(client, "notebook_kernel_restart"))["ok"]

        after = await _call(client, "notebook_session_info")
        assert after["alive"] and after["encryption_enabled"]
        assert after["transport_encryption"] == "required"
        assert after["kernel_generation"] == before["kernel_generation"] + 1
        execution = await _call(client, "notebook_code_execute", code="print(6 * 7)", timeout=10)
        assert execution["ok"] and execution["stdout"] == "42\n"


@pytest.mark.skipif(
    "JAT_COLLAB_URL" not in os.environ, reason="Set JAT_COLLAB_URL to run this test"
)
async def test_shared_mcp_import_survives_reconnect(tmp_path):
    base_url = os.environ["JAT_COLLAB_URL"].rstrip("/")
    token = os.getenv("JAT_COLLAB_TOKEN")
    path = f"shared-import-{uuid4().hex}.ipynb"
    config = NotebookWorkspaceConfig(
        mode="server", base_url=base_url, token=token, collaboration_mode="required"
    )
    table = {
        "notebook_path": path,
        "operation_id": "shared-query",
        "name": "query_rows",
        "columns": ["severity"],
        "rows": [["test-row-value"]],
        "metadata": {"query_row_limit": 5},
    }
    expected_receipt = {
        "ok": True,
        "operation_id": "shared-query",
        "table": "query_rows",
        "row_count": 1,
        "column_count": 1,
    }

    async with NotebookWorkspace(config) as workspace:
        owner = await workspace.open(path)
        kernel_id = owner.kernel.session_info().kernel_id
        cells_before = (await owner.doc.fetch())["cells"]
        table_ids = set()
        for _attempt in range(2):
            async with _connect_mcp(
                tmp_path,
                "--mode",
                "server",
                "--base-url",
                base_url,
                "--collaboration-mode",
                "required",
                "--notebook-path",
                path,
                env={"MCP_JUPYTER_TOKEN": token} if token else None,
            ) as client:
                info = await _call(client, "notebook_session_info")
                assert info["kernel_id"] == kernel_id
                assert not info["owns_kernel"] and not info["owns_session"]
                assert await _call(client, "notebook_import_table", **table) == expected_receipt
                summary = await _call(
                    client,
                    "notebook_code_execute",
                    code="print(id(query_rows), len(query_rows))",
                    timeout=10,
                )
                assert summary["ok"]
                table_id, row_count = summary["stdout"].split()
                assert row_count == "1"
                table_ids.add(table_id)

            result = await owner.kernel.execute(
                "print(query_rows.iloc[0]['severity'], "
                "query_rows.attrs['notebook_import']['query_row_limit'])",
                timeout=10,
            )
            assert result.status == "ok"
            assert result.stdout == "test-row-value 5\n"

        assert len(table_ids) == 1
        assert (await owner.doc.fetch())["cells"] == cells_before


@pytest.mark.parametrize(
    "mode",
    [
        "local",
        pytest.param(
            "server",
            marks=pytest.mark.skipif(
                "JAT_COLLAB_URL" not in os.environ, reason="Set JAT_COLLAB_URL to run this test"
            ),
        ),
    ],
)
async def test_saved_mcp_dataset_replays_without_source(tmp_path, mode):
    import pyarrow.parquet as parquet

    root = tmp_path / "datasets"
    path = f"capture-{uuid4().hex}.ipynb"
    args = ["--mode", mode, "--dataset-root", str(root), "--kernel-dataset-root", str(root)]
    env = {"JUPYTER_RUNTIME_DIR": str(tmp_path / "runtime")}
    if mode == "server":
        args += ["--base-url", os.environ["JAT_COLLAB_URL"], "--collaboration-mode", "required"]
        if token := os.getenv("JAT_COLLAB_TOKEN"):
            env["MCP_JUPYTER_TOKEN"] = token

    plan = {
        "operation_id": "approved-query",
        "name": "query_rows",
        "columns": ["id", "label"],
        "row_count": 6,
        "metadata": {"query_row_limit": 5, "query": "print 1\n| take 5"},
    }
    dataset_tools = {"notebook_dataset_begin", "notebook_dataset_append", "notebook_dataset_commit"}
    first_batch = [["1", "synthetic-private-row-α"], ["2", None]]
    second_batch = [["3", "third"], ["4", "fourth"], ["5", "fifth"], ["6", "outside-preview"]]
    async with _connect_mcp(tmp_path, *args, "--notebook-path", path, env=env) as client:
        listed = {tool.name for tool in (await client.list_tools()).tools}
        assert dataset_tools <= listed
        assert "notebook_import_table" in listed
        started = await _call(client, "notebook_dataset_begin", notebook_path=path, **plan)
        capture = {"notebook_path": path, "dataset_id": started["dataset_id"]}
        assert started["ok"]
        out_of_order = await _call(
            client, "notebook_dataset_append", **capture, index=1, rows=second_batch
        )
        assert out_of_order["error_code"] == "batch_out_of_order"
        first = await _call(client, "notebook_dataset_append", **capture, index=0, rows=first_batch)
        assert first["rows_written"] == 2
        assert (
            await _call(client, "notebook_dataset_append", **capture, index=0, rows=first_batch)
            == first
        )
        incomplete = await _call(client, "notebook_dataset_commit", **capture, batch_count=1)
        assert incomplete["error_code"] == "dataset_incomplete"
        assert not list(root.glob("*/*.parquet"))

    if mode == "local":
        renamed = "renamed.ipynb"
        (tmp_path / path).rename(tmp_path / renamed)
        path = capture["notebook_path"] = renamed

    # The next MCP process and fresh kernel have no in-memory capture state.
    async with _connect_mcp(tmp_path, *args, "--notebook-path", path, env=env) as client:
        assert await _call(client, "notebook_dataset_begin", notebook_path=path, **plan) == started
        final_batch = await _call(
            client, "notebook_dataset_append", **capture, index=1, rows=second_batch
        )
        assert final_batch["rows_written"] == 4
        saved = await _call(client, "notebook_dataset_commit", **capture, batch_count=2)
        assert saved["ok"] and saved["row_count"] == 6 and saved["table"] == "query_rows"
        flagged = await _call(
            client,
            "notebook_code_execute",
            notebook_path=path,
            code="query_rows.attrs['retry_test'] = True",
        )
        assert flagged["ok"]
        assert await _call(client, "notebook_dataset_commit", **capture, batch_count=2) == saved
        checked = await _call(
            client,
            "notebook_code_execute",
            notebook_path=path,
            code="print(query_rows.attrs['retry_test'])",
        )
        assert checked["stdout"] == "True\n"
        assert (
            await _call(client, "notebook_dataset_append", **capture, index=0, rows=first_batch)
        )["ok"]
        conflict = await _call(
            client, "notebook_dataset_append", **capture, index=0, rows=[["1", "changed"]]
        )
        assert conflict["error_code"] == "operation_conflict"

        [snapshot] = root.glob("*/*.parquet")
        assert snapshot.name == f"{saved['dataset_id']}.parquet"
        assert not list(root.glob("*/.pending/*"))
        assert parquet.read_metadata(snapshot).row_group(0).column(0).compression == "ZSTD"
        assert parquet.read_table(snapshot).to_pylist() == [
            {"id": number, "label": label} for number, label in first_batch + second_batch
        ]

        empty = await _call(
            client,
            "notebook_dataset_begin",
            notebook_path=path,
            operation_id="empty-query",
            name="empty_rows",
            columns=[],
            row_count=0,
        )
        assert (
            await _call(
                client,
                "notebook_dataset_commit",
                notebook_path=path,
                dataset_id=empty["dataset_id"],
                batch_count=0,
            )
        )["ok"]

        code = (
            "print(query_rows['id'].astype(int).sum(), query_rows['label'].isna().sum(), "
            "query_rows.attrs['notebook_import']['query_row_limit'], len(empty_rows))"
        )
        before = await _call(client, "notebook_code_run", notebook_path=path, code=code)
        assert before["ok"] and before["stdout"] == "21 1 5 0\n"
        replay = await _call(client, "notebook_restart_and_run_all", notebook_path=path)
        assert replay["ok"] and replay["executed_count"] == 3
        after = await _call(client, "notebook_code_execute", notebook_path=path, code=code)
        assert after["ok"] and after["stdout"] == before["stdout"]

    if mode == "local":
        document = json.loads((tmp_path / path).read_text(encoding="utf-8"))
        query, loader, empty_loader, analysis = document["cells"]
        assert query["id"] == saved["query_cell_id"] and query["cell_type"] == "markdown"
        assert "    | take 5" in "".join(query["source"])
        assert loader["id"] == saved["loader_cell_id"]
        assert loader["metadata"]["ajt_dataset"]["format"] == "parquet"
        source = "".join(loader["source"])
        assert "pd.read_parquet(" in source
        assert "query_rows.head(5)" in source
        assert "query_rows.info(" not in source
        assert all(output["output_type"] != "stream" for output in loader["outputs"])
        assert all(text not in source for text in ("globals()", ".attrs", "take 5"))
        outputs = json.dumps(loader["outputs"], ensure_ascii=False)
        assert "synthetic-private-row" in outputs
        assert "outside-preview" not in outputs
        assert "synthetic-private-row" not in json.dumps(
            [cell["source"] for cell in (query, loader, empty_loader, analysis)]
        )
