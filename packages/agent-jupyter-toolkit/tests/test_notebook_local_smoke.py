import nbformat
import pytest

from agent_jupyter_toolkit.kernel import SessionConfig, create_session
from agent_jupyter_toolkit.notebook import NotebookSession, make_document_transport

pytestmark = pytest.mark.asyncio


async def test_local_notebook_smoke(tmp_path):
    notebook_path = tmp_path / "smoke.ipynb"
    kernel_session = create_session(SessionConfig(mode="local", kernel_name="python3"))
    doc_transport = make_document_transport(
        mode="local",
        local_path=str(notebook_path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
        create_if_missing=True,
    )
    nb_session = NotebookSession(kernel=kernel_session, doc=doc_transport)

    async with nb_session:
        idx, result = await nb_session.append_and_run("print('notebook smoke')")

    assert notebook_path.exists()
    assert idx == 0
    assert result.status == "ok"
    assert "notebook smoke" in result.stdout


async def test_local_notebook_restart_and_run_all_reexecutes_from_clean_kernel(tmp_path):
    notebook_path = tmp_path / "restart_and_run_all.ipynb"
    kernel_session = create_session(SessionConfig(mode="local", kernel_name="python3"))
    doc_transport = make_document_transport(
        mode="local",
        local_path=str(notebook_path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
        create_if_missing=True,
    )
    nb_session = NotebookSession(kernel=kernel_session, doc=doc_transport)

    async with nb_session:
        await nb_session.run_markdown("# restart and run all")
        await nb_session.append_and_run("value = 21")
        await nb_session.append_and_run("print(value * 2)")

        result = await nb_session.restart_and_run_all()

    assert notebook_path.exists()
    assert result.status == "ok"
    assert result.executed_count == 2
    assert result.skipped_count == 1
    assert [cell.status for cell in result.cells] == ["skipped", "ok", "ok"]

    nb = nbformat.read(notebook_path, as_version=4)
    assert nb.cells[2]["execution_count"] is not None
    assert any(
        output.get("output_type") == "stream" and "42" in output.get("text", "")
        for output in nb.cells[2]["outputs"]
    )


async def test_display_update_from_later_cell_updates_original_cell(tmp_path, monkeypatch):
    notebook_path = tmp_path / "cross-cell-display.ipynb"
    session = NotebookSession(
        kernel=create_session(SessionConfig(mode="local", kernel_name="python3")),
        doc=make_document_transport(
            mode="local",
            local_path=str(notebook_path),
            remote_base=None,
            remote_path=None,
            token=None,
            headers_json=None,
            create_if_missing=True,
        ),
    )

    async with session:
        await session.append_and_run(
            "from IPython.display import display\nhandle = display('before', display_id=True)"
        )
        _, result = await session.append_and_run("handle.update('after')")
        assert result.persistence_status == "ok"

        first_cell = await session.doc.get_cell(0)
        update_outputs = session.doc.update_cell_outputs_by_id

        async def fail_original_cell(cell_id, *args, **kwargs):
            if cell_id == first_cell["id"]:
                raise OSError("simulated persistence failure")
            return await update_outputs(cell_id, *args, **kwargs)

        monkeypatch.setattr(session.doc, "update_cell_outputs_by_id", fail_original_cell)
        _, failed_update = await session.append_and_run("handle.update('unpersisted')")

        assert failed_update.status == "ok"
        assert failed_update.persistence_status == "error"
        assert "simulated persistence failure" in (failed_update.persistence_error or "")

    notebook = nbformat.read(notebook_path, as_version=4)
    assert len(notebook.cells[0].outputs) == 1
    assert "after" in notebook.cells[0].outputs[0]["data"]["text/plain"]
