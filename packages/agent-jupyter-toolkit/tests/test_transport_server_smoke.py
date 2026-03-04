import os

import pytest

from agent_jupyter_toolkit.kernel import SessionConfig, create_session
from agent_jupyter_toolkit.kernel.transports.server import ServerConfig

pytestmark = pytest.mark.asyncio

skip_server = pytest.mark.skipif(
    "JAT_SERVER_URL" not in os.environ,
    reason="Set JAT_SERVER_URL (and optionally JAT_SERVER_TOKEN) to run server tests.",
)


@skip_server
async def test_server_execute_ok():
    cfg = SessionConfig(
        mode="server",
        server=ServerConfig(
            base_url=os.environ["JAT_SERVER_URL"].rstrip("/"),
            token=os.getenv("JAT_SERVER_TOKEN"),
            kernel_name="python3",
        ),
    )
    sess = create_session(cfg)
    await sess.start()
    try:
        res = await sess.execute("print('hello from server')\n40+2")
        assert res.status == "ok"
        assert "hello from server" in res.stdout
        assert any(
            o.get("output_type") in ("stream", "execute_result", "display_data")
            for o in res.outputs
        )
    finally:
        await sess.shutdown()
