import asyncio
from pathlib import Path

from agent_jupyter_toolkit.notebook import make_document_transport


async def main() -> None:
    path = Path("quickstarts/output/autosave_demo.ipynb")
    transport = make_document_transport(
        mode="local",
        local_path=str(path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
        create_if_missing=True,
        local_autosave_delay=0.5,
    )

    await transport.start()
    try:
        idx0 = await transport.append_markdown_cell("# Autosave demo")
        idx1 = await transport.append_code_cell("x = 1")
        idx2 = await transport.append_code_cell("x + 41")
        print(f"Appended cells at indices: {idx0}, {idx1}, {idx2}")
        # Wait for the debounce window to flush writes
        await asyncio.sleep(0.75)
    finally:
        await transport.stop()

    print(f"Notebook saved at: {path}")


if __name__ == "__main__":
    asyncio.run(main())
