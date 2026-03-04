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
