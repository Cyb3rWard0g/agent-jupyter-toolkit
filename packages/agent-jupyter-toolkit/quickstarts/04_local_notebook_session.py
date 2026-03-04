import asyncio
import os

from agent_jupyter_toolkit.kernel import SessionConfig, create_session
from agent_jupyter_toolkit.notebook import NotebookSession, make_document_transport


async def main() -> None:
    notebook_path = os.getenv("LOCAL_NOTEBOOK_PATH", "quickstarts/output/local_demo.ipynb")

    kernel_session = create_session(SessionConfig(mode="local", kernel_name="python3"))
    doc_transport = make_document_transport(
        mode="local",
        local_path=notebook_path,
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
        create_if_missing=True,
    )
    notebook_session = NotebookSession(kernel=kernel_session, doc=doc_transport)

    async with notebook_session:
        await notebook_session.run_markdown("# Local notebook quickstart")
        idx, result = await notebook_session.append_and_run(
            "import datetime\n" "print('timestamp:', datetime.datetime.utcnow().isoformat())\n"
        )

    print("notebook_path:", notebook_path)
    print("cell_index:", idx)
    print("status:", result.status)
    print("execution_count:", result.execution_count)


if __name__ == "__main__":
    asyncio.run(main())
