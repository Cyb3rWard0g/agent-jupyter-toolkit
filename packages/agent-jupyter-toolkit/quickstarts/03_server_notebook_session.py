import asyncio
import os

from agent_jupyter_toolkit.kernel import ServerConfig, SessionConfig, create_session
from agent_jupyter_toolkit.notebook import NotebookSession, make_document_transport


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing required env var: {name}")
    return value


async def main() -> None:
    base_url = _require_env("JUPYTER_BASE_URL")
    notebook_path = _require_env("JUPYTER_NOTEBOOK_PATH")
    token = os.getenv("JUPYTER_TOKEN")
    kernel_name = os.getenv("JUPYTER_KERNEL_NAME", "python3")
    headers_json = os.getenv("JUPYTER_HEADERS_JSON")
    prefer_collab = os.getenv("JUPYTER_PREFER_COLLAB") == "1"

    server = ServerConfig(
        base_url=base_url,
        token=token,
        kernel_name=kernel_name,
        notebook_path=notebook_path,
    )
    kernel_session = create_session(SessionConfig(mode="server", server=server))

    try:
        doc_transport = make_document_transport(
            mode="server",
            local_path=None,
            remote_base=base_url,
            remote_path=notebook_path,
            token=token,
            headers_json=headers_json,
            prefer_collab=prefer_collab,
            create_if_missing=True,
        )
    except ImportError as exc:
        raise SystemExit(
            "Collaborative transport requested but dependencies are missing. "
            "Install the collab extras or set JUPYTER_PREFER_COLLAB=0."
        ) from exc

    notebook_session = NotebookSession(kernel=kernel_session, doc=doc_transport)

    async with notebook_session:
        idx, result = await notebook_session.append_and_run(
            "import math\nprint('pi:', math.pi)\nprint('tau:', math.tau)\n"
        )

    print("cell_index:", idx)
    print("status:", result.status)
    print("execution_count:", result.execution_count)
    print("stdout:", result.stdout.strip())


if __name__ == "__main__":
    asyncio.run(main())
