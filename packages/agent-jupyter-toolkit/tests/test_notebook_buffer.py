import pytest

from agent_jupyter_toolkit.notebook import NotebookBuffer, make_document_transport

pytestmark = pytest.mark.asyncio


async def test_notebook_buffer_commit(tmp_path):
    notebook_path = tmp_path / "buffer.ipynb"
    transport = make_document_transport(
        mode="local",
        local_path=str(notebook_path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
        create_if_missing=True,
    )

    await transport.start()
    try:
        buffer = NotebookBuffer(transport)
        await buffer.load()
        buffer.append_markdown_cell("# Buffer test")
        await buffer.commit()
    finally:
        await transport.stop()

    assert notebook_path.exists()
