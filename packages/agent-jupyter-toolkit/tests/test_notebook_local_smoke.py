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
