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
