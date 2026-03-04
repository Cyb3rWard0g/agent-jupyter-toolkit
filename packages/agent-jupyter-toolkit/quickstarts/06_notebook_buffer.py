import asyncio
from pathlib import Path

from agent_jupyter_toolkit.notebook import NotebookBuffer, make_document_transport


async def main() -> None:
    path = Path("quickstarts/output/buffer_demo.ipynb")
    transport = make_document_transport(
        mode="local",
        local_path=str(path),
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

        idx0 = buffer.append_markdown_cell("# Buffered edits")
        idx1 = buffer.append_code_cell("print('buffered run')")
        print(f"Buffered cells at indices: {idx0}, {idx1}")
        buffer.update_cell_outputs(
            idx1,
            [{"output_type": "stream", "name": "stdout", "text": "buffered run\n"}],
            execution_count=1,
        )

        await buffer.commit()
    finally:
        await transport.stop()

    print(f"Notebook saved at: {path}")


if __name__ == "__main__":
    asyncio.run(main())
