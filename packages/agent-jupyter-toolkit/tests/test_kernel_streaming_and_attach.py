import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent_jupyter_toolkit.kernel import SessionConfig, create_session
from agent_jupyter_toolkit.kernel.execution_state import ExecutionState
from agent_jupyter_toolkit.kernel.messages import fold_iopub_events
from agent_jupyter_toolkit.kernel.transports.local import LocalTransport

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


async def test_local_callback_arrives_before_execution_finishes():
    sess = create_session(SessionConfig(mode="local", kernel_name="python3"))
    await sess.start()
    try:
        streamed = asyncio.Event()

        async def output_callback(outputs, _execution_count):
            if any("before sleep" in output.get("text", "") for output in outputs):
                streamed.set()

        task = asyncio.create_task(
            sess.execute(
                "import time\nprint('before sleep', flush=True)\ntime.sleep(1)",
                output_callback=output_callback,
            )
        )
        await asyncio.wait_for(streamed.wait(), timeout=0.75)
        assert not task.done()
        assert (await task).status == "ok"
    finally:
        await sess.shutdown()


async def test_local_concurrent_executions_keep_their_own_messages():
    sess = create_session(SessionConfig(mode="local", kernel_name="python3"))
    await sess.start()
    try:
        first, second = await asyncio.gather(
            sess.execute("import time; time.sleep(0.1); print('first-only')"),
            sess.execute("print('second-only')"),
        )
        assert "first-only" in first.stdout
        assert "second-only" not in first.stdout
        assert "second-only" in second.stdout
        assert "first-only" not in second.stdout
    finally:
        await sess.shutdown()


async def test_fold_honors_deferred_clear_and_display_update():
    deferred = fold_iopub_events(
        [
            {"msg_type": "stream", "content": {"name": "stdout", "text": "preserve\n"}},
            {"msg_type": "clear_output", "content": {"wait": True}},
        ]
    )
    assert deferred.stdout == "preserve\n"

    updated = fold_iopub_events(
        [
            {
                "msg_type": "display_data",
                "content": {
                    "data": {"text/plain": "before"},
                    "metadata": {},
                    "transient": {"display_id": "display-1"},
                },
            },
            {
                "msg_type": "update_display_data",
                "content": {
                    "data": {"text/plain": "after"},
                    "metadata": {},
                    "transient": {"display_id": "display-1"},
                },
            },
        ]
    )
    assert len(updated.outputs) == 1
    assert updated.outputs[0]["data"]["text/plain"] == "after"
    assert "transient" not in updated.outputs[0]


async def test_execution_state_bounds_and_reports_stream_output():
    state = ExecutionState(max_output_bytes=5)
    state.apply({"msg_type": "stream", "content": {"name": "stdout", "text": "12345678"}})

    result = state.result()

    assert result.stdout == "12345"
    assert result.output_truncated is True
    assert result.dropped_output_bytes == 3


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
        await sess2.start()  # Attachment startup is also idempotent.
        try:
            assert await sess2.is_alive()
            res = await sess2.execute("x = 10\nx")
            assert res.status == "ok"
        finally:
            await sess2.shutdown()
        still_running = await sess1.execute("x + 1")
        assert still_running.status == "ok"
        assert still_running.outputs[0]["data"]["text/plain"] == "11"
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


async def test_display_update_sidecars_share_output_budget_after_clear():
    state = ExecutionState(max_output_bytes=180)
    for index in range(20):
        state.apply(
            {
                "msg_type": "update_display_data",
                "content": {
                    "data": {"text/plain": "x" * 40},
                    "transient": {"display_id": f"display-{index}"},
                },
            }
        )
        state.apply({"msg_type": "clear_output", "content": {"wait": False}})
    result = state.result()
    assert len(json.dumps(result.display_updates).encode()) <= 180
    assert result.output_truncated
    assert result.dropped_output_bytes > 0


async def test_oversized_display_update_keeps_prior_visible_output():
    state = ExecutionState(max_output_bytes=200)
    for kind, value in [("display_data", "before"), ("update_display_data", "x" * 400)]:
        state.apply(
            {
                "msg_type": kind,
                "content": {"data": {"text/plain": value}, "transient": {"display_id": "d"}},
            }
        )
    result = state.result()
    assert result.outputs[0]["data"]["text/plain"] == "before"
    assert result.display_updates == {}
    assert result.output_truncated


async def test_cancelling_execution_cancels_a_blocked_output_callback():
    callback_started = asyncio.Event()
    blocked = asyncio.Event()

    async def callback(*_args):
        callback_started.set()
        await blocked.wait()

    async def execute_interactive(*_args, output_hook, **_kwargs):
        output_hook({"msg_type": "stream", "content": {"name": "stdout", "text": "first"}})
        await blocked.wait()

    transport = LocalTransport()
    transport.kernel_manager._kc = SimpleNamespace(
        execute=Mock(), execute_interactive=AsyncMock(side_effect=execute_interactive)
    )
    task = asyncio.create_task(transport.execute("code", output_callback=callback))
    await asyncio.wait_for(callback_started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert not any(t.get_name() == "ajt-output-callback" for t in asyncio.all_tasks())
