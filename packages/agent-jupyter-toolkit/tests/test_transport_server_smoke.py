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


@skip_server
async def test_server_restart_clears_namespace_and_keeps_session_usable():
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
        first = await sess.execute("x = 41")
        assert first.status == "ok"

        before_restart = await sess.execute("print(x)")
        assert before_restart.status == "ok"
        assert "41" in before_restart.stdout

        await sess.restart()

        after_restart = await sess.execute("print('server kernel alive after restart')")
        assert after_restart.status == "ok"
        assert "server kernel alive after restart" in after_restart.stdout

        missing_name = await sess.execute("x")
        assert missing_name.status == "error"
        assert any(
            output.get("output_type") == "error" and output.get("ename") == "NameError"
            for output in missing_name.outputs
        )
    finally:
        await sess.shutdown()
