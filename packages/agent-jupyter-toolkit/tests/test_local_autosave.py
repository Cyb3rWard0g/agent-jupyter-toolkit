import nbformat
import pytest

from agent_jupyter_toolkit.notebook import make_document_transport

pytestmark = pytest.mark.asyncio


async def test_local_autosave_flushes_on_stop(tmp_path):
    notebook_path = tmp_path / "autosave.ipynb"
    transport = make_document_transport(
        mode="local",
        local_path=str(notebook_path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
        create_if_missing=True,
        local_autosave_delay=0.2,
    )

    await transport.start()
    try:
        await transport.append_markdown_cell("# Autosave")
        await transport.append_code_cell("x = 1")
        await transport.append_code_cell("x + 1")
    finally:
        await transport.stop()

    nb = nbformat.read(notebook_path, as_version=4)
    assert len(nb.cells) == 3
    assert nb.cells[0]["cell_type"] == "markdown"
