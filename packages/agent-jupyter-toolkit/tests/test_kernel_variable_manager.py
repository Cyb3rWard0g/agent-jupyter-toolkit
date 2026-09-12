import pytest

from agent_jupyter_toolkit.kernel import SessionConfig, create_session
from agent_jupyter_toolkit.kernel.variables import VariableManager

pytestmark = pytest.mark.asyncio


async def test_kernel_variable_manager():
    sess = create_session(SessionConfig(mode="local", kernel_name="python3"))
    await sess.start()
    try:
        var_mgr = VariableManager(sess)
        # Create variable
        await sess.execute("x = 123")
        var_list = await var_mgr.list()
        assert "x" in var_list
        # Get variable
        value = await var_mgr.get("x")
        assert value == 123 or str(value) == "123"
        detailed = await var_mgr.list(detailed=True)
        x = next(item for item in detailed if item["name"] == "x")
        assert x["type"] == ["builtins", "int"]
        assert isinstance(x["size"], int)
        assert "_ajt_describe_variables" not in await var_mgr.list()
    finally:
        await sess.shutdown()
