import asyncio
import os

from agent_jupyter_toolkit.notebook import make_document_transport


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing required env var: {name}")
    return value


async def main() -> None:
    base_url = _require_env("JUPYTER_BASE_URL")
    notebook_path = _require_env("JUPYTER_NOTEBOOK_PATH")
    token = os.getenv("JUPYTER_TOKEN")
    username = os.getenv("JUPYTER_USERNAME", "agent")

    transport = make_document_transport(
        mode="server",
        local_path=None,
        remote_base=base_url,
        remote_path=notebook_path,
        token=token,
        headers_json=None,
        prefer_collab=True,
        create_if_missing=True,
    )

    try:
        await transport.start()
        if hasattr(transport, "append_markdown_cell"):
            await transport.append_markdown_cell("# Collab awareness demo")
        # Set local awareness state (visible to other collab clients)
        if hasattr(transport, "set_awareness_field"):
            await transport.set_awareness_field(
                "user",
                {
                    "name": username,
                    "role": "agent",
                    "note": "collab awareness demo",
                },
            )

        if hasattr(transport, "get_awareness_states"):
            states = transport.get_awareness_states()
            print("awareness states:", states)
        print("note: awareness metadata is not persisted in the notebook file")
        # Keep the session alive briefly so the server can flush its messages cleanly.
        await asyncio.sleep(2)
    finally:
        # Ensure sessions/sockets are closed even if start() fails
        try:
            await transport.stop()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
