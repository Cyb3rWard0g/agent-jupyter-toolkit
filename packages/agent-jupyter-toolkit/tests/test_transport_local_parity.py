import pytest

from agent_jupyter_toolkit.kernel import SessionConfig, create_session

pytestmark = pytest.mark.asyncio


async def test_local_execute_ok():
    sess = create_session(SessionConfig(mode="local", kernel_name="python3"))
    await sess.start()
    try:
        res = await sess.execute("print('hi')\n1+2")
        assert res.status == "ok"
        assert "hi" in res.stdout
        # at least one meaningful output (stream or result)
        assert any(
            o["output_type"] in ("stream", "execute_result", "display_data") for o in res.outputs
        )
        # execution_count should be int or None, but not other types
        assert (res.execution_count is None) or isinstance(res.execution_count, int)
    finally:
        await sess.shutdown()


async def test_local_restart_clears_namespace_and_keeps_session_usable():
    sess = create_session(SessionConfig(mode="local", kernel_name="python3"))
    await sess.start()
    try:
        first = await sess.execute("x = 41")
        assert first.status == "ok"

        before_restart = await sess.execute("print(x)")
        assert before_restart.status == "ok"
        assert "41" in before_restart.stdout

        await sess.restart()

        after_restart = await sess.execute("print('kernel alive after restart')")
        assert after_restart.status == "ok"
        assert "kernel alive after restart" in after_restart.stdout

        missing_name = await sess.execute("x")
        assert missing_name.status == "error"
        assert any(
            output.get("output_type") == "error" and output.get("ename") == "NameError"
            for output in missing_name.outputs
        )
    finally:
        await sess.shutdown()
