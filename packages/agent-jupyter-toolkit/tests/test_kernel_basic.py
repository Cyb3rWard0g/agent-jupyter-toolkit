import pytest
from packaging.version import Version

from agent_jupyter_toolkit.kernel import SessionConfig, create_session
from agent_jupyter_toolkit.utils.packages import check_package_availability

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


async def test_kernel_subshell_lifecycle_and_execution():
    sess = create_session(SessionConfig(mode="local", kernel_name="python3"))
    await sess.start()
    subshell_id = None
    try:
        info = await sess.kernel_info()
        if "kernel subshells" not in info.supported_features:
            pytest.skip("Kernel does not advertise subshell support")
        subshell_id = await sess.create_subshell()
        assert subshell_id in await sess.list_subshells()
        result = await sess.execute("print('from subshell')", subshell_id=subshell_id)
        assert result.status == "ok"
        assert result.stdout == "from subshell\n"

        if info.implementation == "ipython" and Version(info.implementation_version) >= Version(
            "7.4"
        ):
            deleted_id = subshell_id
            await sess.delete_subshell(deleted_id)
            subshell_id = None
            rejected = await sess.execute("pass", subshell_id=deleted_id, timeout=5)
            assert rejected.status == "error"
            assert not rejected.timed_out
            assert any(out.get("ename") == "UnknownSubshellError" for out in rejected.outputs)
            assert (await sess.execute("21 * 2", timeout=5)).status == "ok"
    finally:
        if subshell_id is not None:
            await sess.delete_subshell(subshell_id)
        await sess.shutdown()


async def test_package_availability_honors_pep508_constraints():
    sess = create_session(SessionConfig(mode="local", kernel_name="python3"))
    await sess.start()
    try:
        with pytest.raises(ValueError, match="Invalid package requirement"):
            await check_package_availability(sess, ["not a valid requirement!!"])
        with pytest.raises(ValueError, match="Direct URL requirements are not supported"):
            await check_package_availability(
                sess,
                ["packaging @ https://example.invalid/packaging.whl"],
            )
        available = await check_package_availability(
            sess,
            [
                "packaging>=25",
                "packaging>9999",
                "packaging[missing-extra]",
                "missing-jat-package[extra]; python_version < '1'",
            ],
        )
        assert available == {
            "packaging>=25": True,
            "packaging>9999": False,
            "packaging[missing-extra]": False,
            "missing-jat-package[extra]; python_version < '1'": True,
        }
    finally:
        await sess.shutdown()
