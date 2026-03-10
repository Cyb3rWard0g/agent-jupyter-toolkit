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


async def test_notebook_buffer_cell_id_reads_and_move(tmp_path):
    notebook_path = tmp_path / "buffer_move.ipynb"
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
        buffer.append_code_cell("a = 1")
        buffer.append_markdown_cell("# middle")
        buffer.append_code_cell("b = 2")

        middle_id = buffer[1]["id"]

        assert buffer.resolve_cell_index(middle_id) == 1
        assert buffer.get_cell_by_id(middle_id)["source"] == "# middle"

        buffer.move_cell(0, 2)

        assert [cell["source"] for cell in buffer] == ["# middle", "b = 2", "a = 1"]
        assert buffer.dirty is True
    finally:
        await transport.stop()
