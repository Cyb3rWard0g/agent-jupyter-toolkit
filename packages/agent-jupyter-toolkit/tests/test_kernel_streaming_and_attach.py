import asyncio

import pytest

from agent_jupyter_toolkit.kernel import SessionConfig, create_session
from agent_jupyter_toolkit.kernel.messages import fold_iopub_events

pytestmark = pytest.mark.asyncio


async def test_local_kernel_output_streaming():
    sess = create_session(SessionConfig(mode="local", kernel_name="python3"))
    await sess.start()
    try:
        got_output = asyncio.Event()
        snapshots: list[list[dict]] = []
        exec_counts: list[int | None] = []

        async def output_callback(outputs, execution_count):
            snapshots.append(list(outputs or []))
            exec_counts.append(execution_count)
            if any(o.get("output_type") == "stream" for o in outputs or []):
                got_output.set()

        res = await sess.execute(
            "print('streamed')\n1+1",
            output_callback=output_callback,
        )

        assert res.status == "ok"
        assert got_output.is_set()
        assert snapshots
        assert any("streamed" in o.get("text", "") for s in snapshots for o in s)

        events = [
            {"header": {"msg_type": "execute_input"}, "content": {"execution_count": 1}},
            {"header": {"msg_type": "stream"}, "content": {"name": "stdout", "text": "hi\n"}},
            {
                "header": {"msg_type": "execute_result"},
                "content": {"data": {"text/plain": "2"}, "metadata": {}, "execution_count": 1},
            },
            {
                "header": {"msg_type": "execute_reply"},
                "content": {"status": "ok", "execution_count": 1},
            },
        ]
        folded = fold_iopub_events(events)
        assert folded.status == "ok"
        assert folded.stdout == "hi\n"
        assert folded.execution_count == 1
        assert any(o.get("output_type") == "execute_result" for o in folded.outputs)
    finally:
        await sess.shutdown()


async def test_connection_file_attach():
    sess1 = create_session(SessionConfig(mode="local", kernel_name="python3"))
    await sess1.start()
    try:
        km = sess1.kernel_manager
        assert km is not None
        connection_path = km.connection_file_path
        assert connection_path

        sess2 = create_session(
            SessionConfig(
                mode="local",
                connection_file_name=connection_path,
            )
        )
        await sess2.start()
        try:
            res = await sess2.execute("x = 10\nx")
            assert res.status == "ok"
        finally:
            await sess2.shutdown()
    finally:
        await sess1.shutdown()


async def test_local_kernel_timeout_still_emits_callback():
    sess = create_session(SessionConfig(mode="local", kernel_name="python3"))
    await sess.start()
    try:
        snapshots: list[list[dict]] = []
        exec_counts: list[int | None] = []

        async def output_callback(outputs, execution_count):
            snapshots.append(list(outputs or []))
            exec_counts.append(execution_count)

        res = await sess.execute(
            "import time\nwhile True:\n    time.sleep(0.1)",
            timeout=0.2,
            output_callback=output_callback,
        )

        assert res.status == "error"
        assert snapshots, "Expected at least the final callback snapshot on timeout"
        assert len(exec_counts) == len(snapshots)
    finally:
        await sess.shutdown()
