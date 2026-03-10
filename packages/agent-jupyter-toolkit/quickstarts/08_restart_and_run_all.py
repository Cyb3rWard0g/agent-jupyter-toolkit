import asyncio
import os

from agent_jupyter_toolkit.kernel import SessionConfig, create_session
from agent_jupyter_toolkit.notebook import NotebookSession, make_document_transport


async def main() -> None:
    notebook_path = os.getenv(
        "LOCAL_NOTEBOOK_PATH",
        "quickstarts/output/restart_and_run_all_demo.ipynb",
    )

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
        await notebook_session.run_markdown("# Reproducibility quickstart")
        await notebook_session.append_and_run("value = 21")
        await notebook_session.append_and_run("print('double:', value * 2)")

        result = await notebook_session.restart_and_run_all()

    print("notebook_path:", notebook_path)
    print("status:", result.status)
    print("executed_count:", result.executed_count)
    print("skipped_count:", result.skipped_count)
    if result.first_failure is not None:
        print("first_failure_index:", result.first_failure.index)
        print("first_failure_error:", result.first_failure.error_message)


if __name__ == "__main__":
    asyncio.run(main())
