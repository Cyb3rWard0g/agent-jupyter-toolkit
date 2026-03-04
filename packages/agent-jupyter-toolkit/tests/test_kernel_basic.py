import pytest

from agent_jupyter_toolkit.kernel import SessionConfig, create_session

pytestmark = pytest.mark.asyncio


async def test_kernel_basic():
    sess = create_session(SessionConfig(mode="local", kernel_name="python3"))
    await sess.start()
    try:
        res = await sess.execute("print('Hello from kernel!')\nx = 42\nx")
        assert res.status == "ok"
        assert "Hello from kernel!" in res.stdout
    finally:
        await sess.shutdown()
