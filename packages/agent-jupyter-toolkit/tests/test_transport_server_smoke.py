import asyncio
import os

import pytest

from agent_jupyter_toolkit.kernel import (
    KernelDisconnectedError,
    SessionConfig,
    create_session,
)
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
async def test_server_optional_control_channel_workflows():
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
    subshell_id = None
    try:
        info = await sess.kernel_info()
        if "debugger" in info.supported_features:
            response = await sess.debug({"seq": 1, "type": "request", "command": "debugInfo"})
            assert response.get("success") is not False
        if "kernel subshells" in info.supported_features:
            subshell_id = await sess.create_subshell()
            assert subshell_id in await sess.list_subshells()
            result = await sess.execute("print('remote subshell')", subshell_id=subshell_id)
            assert result.status == "ok"
            assert result.stdout == "remote subshell\n"
    finally:
        if subshell_id is not None:
            await sess.delete_subshell(subshell_id)
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


@skip_server
async def test_notebook_session_reuse_and_borrowed_shutdown_preserves_kernel():
    server = ServerConfig(
        base_url=os.environ["JAT_SERVER_URL"].rstrip("/"),
        token=os.getenv("JAT_SERVER_TOKEN"),
        kernel_name="python3",
        notebook_path="shared/session-ownership.ipynb",
    )
    owner = create_session(SessionConfig(mode="server", server=server))
    borrower = create_session(SessionConfig(mode="server", server=server))
    await owner.start()
    try:
        await owner.execute("shared_value = 41")
        await borrower.start()
        assert borrower.session_info().kernel_id == owner.session_info().kernel_id
        assert borrower.session_info().owns_kernel is False
        await borrower.shutdown()

        result = await owner.execute("shared_value + 1")
        assert result.outputs[-1]["data"]["text/plain"] == "42"
    finally:
        await borrower.shutdown()
        await owner.shutdown()


@skip_server
async def test_server_disconnect_finishes_unlimited_execution_with_typed_error():
    session = create_session(
        SessionConfig(
            mode="server",
            server=ServerConfig(
                base_url=os.environ["JAT_SERVER_URL"].rstrip("/"),
                token=os.getenv("JAT_SERVER_TOKEN"),
            ),
        )
    )
    await session.start()
    try:
        started = asyncio.Event()

        async def on_output(outputs, _execution_count):
            if any("started" in output.get("text", "") for output in outputs):
                started.set()

        task = asyncio.create_task(
            session.execute(
                "import time\nprint('started', flush=True)\ntime.sleep(30)",
                output_callback=on_output,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=3)
        await session._transport._ws.close()
        with pytest.raises(KernelDisconnectedError) as raised:
            await asyncio.wait_for(task, timeout=3)
        assert "started" in raised.value.partial_result.stdout
        assert raised.value.partial_result.outcome == "unknown"
    finally:
        await session.shutdown()
