import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import zmq

from agent_jupyter_toolkit.kernel import ServerConfig, SessionConfig, create_session
from agent_jupyter_toolkit.kernel.manager import KernelManager
from agent_jupyter_toolkit.notebook import NotebookWorkspaceConfig
from agent_jupyter_toolkit.utils.factories import create_kernel

pytestmark = pytest.mark.asyncio


async def test_explicit_missing_connection_file_does_not_launch_kernel(tmp_path):
    session = create_session(
        SessionConfig(connection_file_name=str(tmp_path / "missing-kernel.json"))
    )

    with pytest.raises(FileNotFoundError, match="does not exist"):
        await session.start()
    assert await session.is_alive() is False


async def test_attachment_rejects_launch_only_options(tmp_path):
    with pytest.raises(ValueError, match="only apply when launching"):
        create_session(
            SessionConfig(
                connection_file_name=str(tmp_path / "kernel.json"),
                cwd=str(tmp_path),
            )
        )


async def test_server_entry_points_reject_local_encryption_policy():
    base_url = "https://jupyter.example.test"
    with pytest.raises(ValueError, match="transport_encryption.*local kernel"):
        create_session(
            SessionConfig(
                mode="server",
                server=ServerConfig(base_url=base_url),
                transport_encryption="required",
            )
        )
    with pytest.raises(ValueError, match="transport_encryption.*local kernel"):
        create_kernel("remote", base_url=base_url, transport_encryption="required")
    with pytest.raises(ValueError, match="transport_encryption.*local kernel"):
        NotebookWorkspaceConfig(mode="server", base_url=base_url, transport_encryption="required")


async def test_failed_attachment_clears_partial_client(tmp_path):
    connection_file = tmp_path / "invalid-kernel.json"
    connection_file.write_text("{}")
    manager = KernelManager(startup_timeout=0.1)

    # Heartbeat failure and readiness timeout can win the race on an invalid
    # connection. Either path must discard the partially initialized client.
    with pytest.raises(RuntimeError):
        await manager.connect_to_existing(str(connection_file))
    assert manager.client is None


@pytest.mark.parametrize("failure", [RuntimeError("launch failed"), asyncio.CancelledError()])
async def test_partial_kernel_launch_is_cleaned_up(monkeypatch, failure):
    upstream = MagicMock()
    upstream.start_kernel = AsyncMock(side_effect=failure)
    upstream.shutdown_kernel = AsyncMock()
    monkeypatch.setattr(
        "agent_jupyter_toolkit.kernel.manager.AsyncKernelManager", lambda **_kw: upstream
    )
    manager = KernelManager()
    with pytest.raises(type(failure)):
        await manager.start()
    upstream.shutdown_kernel.assert_awaited_once_with(now=True)
    assert manager.client is None
    assert manager.owns_kernel is False


async def test_launch_cwd_env_metadata_and_user_expressions(tmp_path):
    session = create_session(
        SessionConfig(
            cwd=str(tmp_path),
            env={"JAT_TEST_ENV": "configured"},
        )
    )
    await session.start()
    try:
        result = await session.execute(
            "import os\nvalue = 40\n"
            "print(os.getcwd())\n"
            "print(os.environ['JAT_TEST_ENV'])\n"
            "get_ipython().kernel.get_parent()['metadata']['cellId']",
            user_expressions={"answer": "value + 2"},
            metadata={"cellId": "configured-cell"},
        )
        info = session.session_info()
    finally:
        await session.shutdown()

    assert str(tmp_path) in result.stdout
    assert "configured" in result.stdout
    assert result.outputs[-1]["data"]["text/plain"] == "'configured-cell'"
    assert result.user_expressions["answer"]["data"]["text/plain"] == "42"
    assert result.request_id
    assert info.kernel_id
    assert info.owns_kernel is True


@pytest.mark.skipif(not zmq.has("curve"), reason="libzmq has no Curve support")
async def test_curve_auto_reports_negotiated_encryption():
    session = create_session(SessionConfig(transport_encryption="auto"))
    await session.start()
    try:
        result = await session.execute("1 + 1")
        info = session.session_info()
    finally:
        await session.shutdown()

    assert result.status == "ok"
    assert info.transport_encryption == "auto"
    assert info.encryption_enabled is True


@pytest.mark.skipif(not zmq.has("curve"), reason="libzmq has no Curve support")
async def test_curve_policy_for_kernelspec_without_encryption_marker(monkeypatch, tmp_path):
    from ipykernel.kernelspec import get_kernel_dict

    spec = get_kernel_dict()
    spec["metadata"].pop("supported_encryption", None)
    spec_dir = tmp_path / "kernels" / "unmarked-python"
    spec_dir.mkdir(parents=True)
    (spec_dir / "kernel.json").write_text(json.dumps(spec), encoding="utf-8")
    monkeypatch.setenv("JUPYTER_PATH", str(tmp_path))

    required = create_session(
        SessionConfig(kernel_name="unmarked-python", transport_encryption="required")
    )
    with pytest.raises(RuntimeError, match="kernelspec does not declare"):
        await required.start()
    assert await required.is_alive() is False

    async with create_session(
        SessionConfig(kernel_name="unmarked-python", transport_encryption="auto")
    ) as session:
        assert (await session.execute("21 * 2", timeout=10)).status == "ok"
        assert session.session_info().encryption_enabled is False
