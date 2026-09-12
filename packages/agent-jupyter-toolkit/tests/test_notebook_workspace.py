"""Concurrency and default selection tests for the core notebook workspace."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_jupyter_toolkit.notebook import NotebookWorkspace, NotebookWorkspaceConfig


@pytest.fixture
def workspace(monkeypatch):
    workspace = NotebookWorkspace()
    monkeypatch.setattr(
        workspace,
        "_build_session",
        lambda path: SimpleNamespace(
            start=AsyncMock(), stop=AsyncMock(), doc=SimpleNamespace(), path=path
        ),
    )
    return workspace


@pytest.mark.asyncio
async def test_default_switching_normalizes_paths_and_preserves_open_sessions(
    workspace, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    async with workspace:
        analysis = await workspace.open("analysis.ipynb")
        assert workspace.get() is analysis
        assert workspace.is_default("./analysis.ipynb")

        report = await workspace.open("report.ipynb")
        assert workspace.get("./report.ipynb") is report
        assert workspace.get() is analysis

        workspace.set_default("./report.ipynb")
        assert workspace.default_path == str(tmp_path / "report.ipynb")
        assert workspace.get() is report
        assert [s["is_default"] for s in workspace.list_sessions()] == [False, True]

        assert await workspace.open("./analysis.ipynb") is analysis
        assert workspace.get() is report
        workspace.set_default("analysis.ipynb")
        assert workspace.get() is analysis
        analysis.start.assert_awaited_once()
        analysis.stop.assert_not_awaited()
        report.stop.assert_not_awaited()

        with pytest.raises(ValueError, match="not open"):
            workspace.set_default("missing.ipynb")
        assert workspace.get() is analysis

        # Existing integrations assigning default_path also get normalization.
        workspace.default_path = "./report.ipynb"
        assert workspace.get() is report
        assert await workspace.close("./report.ipynb")
        assert workspace.get() is analysis
        assert await workspace.close("analysis.ipynb")
        assert workspace.default_path is None
        with pytest.raises(ValueError, match="No notebook_path"):
            workspace.get()


@pytest.mark.asyncio
async def test_close_retargets_before_slow_shutdown_and_preserves_later_switch(
    workspace, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    async with workspace:
        first = await workspace.open("first.ipynb")
        second = await workspace.open("second.ipynb")
        third = await workspace.open("third.ipynb")
        stopping = asyncio.Event()
        finish = asyncio.Event()

        async def slow_stop():
            stopping.set()
            await finish.wait()

        first.stop.side_effect = slow_stop
        closing = asyncio.create_task(workspace.close("first.ipynb"))
        try:
            await stopping.wait()
            assert workspace.get() is second
            workspace.set_default("third.ipynb")
        finally:
            finish.set()
            await closing
        assert workspace.get() is third


@pytest.mark.asyncio
async def test_failed_open_preserves_default_and_cleans_partial_session(
    workspace, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    async with workspace:
        original = await workspace.open("original.ipynb")
        failed = SimpleNamespace(
            start=AsyncMock(side_effect=RuntimeError("startup failed")), stop=AsyncMock()
        )
        monkeypatch.setattr(workspace, "_build_session", lambda path: failed)
        with pytest.raises(RuntimeError, match="startup failed"):
            await workspace.open("failed.ipynb")
        assert workspace.get() is original
        assert len(workspace) == 1
        failed.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_context_entry_opens_preselected_default_and_exit_closes_it(
    workspace, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    workspace.default_path = "./initial.ipynb"
    with pytest.raises(ValueError, match="workflow failed"):
        async with workspace:
            session = workspace.get()
            raise ValueError("workflow failed")
    session.stop.assert_awaited_once()
    assert len(workspace) == 0
    with pytest.raises(RuntimeError, match="shutting down"):
        async with workspace:
            pass


@pytest.mark.asyncio
async def test_cancelled_context_entry_drains_startup_and_closes_session(
    workspace, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_start():
        started.set()
        await release.wait()

    session = SimpleNamespace(start=AsyncMock(side_effect=slow_start), stop=AsyncMock())
    monkeypatch.setattr(workspace, "_build_session", lambda path: session)
    workspace.default_path = "initial.ipynb"
    entering = asyncio.create_task(workspace.__aenter__())
    try:
        await started.wait()
        entering.cancel()
        await asyncio.sleep(0)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await entering
    session.stop.assert_awaited_once()
    assert len(workspace) == 0
    with pytest.raises(RuntimeError, match="shutting down"):
        await workspace.open("later.ipynb")


@pytest.mark.asyncio
async def test_local_workspace_switching_keeps_kernel_state_and_saved_notebooks(tmp_path):
    import nbformat

    async with NotebookWorkspace(default_path=str(tmp_path / "analysis.ipynb")) as workspace:
        analysis = workspace.get()
        _, first = await analysis.append_and_run("x = 10")
        assert first.status == "ok"

        report_path = str(tmp_path / "report.ipynb")
        report = await workspace.open(report_path)
        workspace.set_default(report_path)
        _, second = await workspace.get().append_and_run("x = 99\nprint(x)")
        assert second.status == "ok"
        assert second.stdout.strip() == "99"
        assert workspace.get(str(tmp_path / "analysis.ipynb")) is analysis
        assert workspace.get() is report

        workspace.set_default(str(tmp_path / "analysis.ipynb"))
        _, resumed = await workspace.get().append_and_run("print(x)")
        assert resumed.status == "ok"
        assert resumed.stdout.strip() == "10"

    assert len(workspace) == 0
    for path, expected in [("analysis.ipynb", "10"), ("report.ipynb", "99")]:
        notebook = nbformat.read(tmp_path / path, as_version=4)
        assert notebook.cells[-1].outputs[0].text.strip() == expected


@pytest.mark.asyncio
async def test_concurrent_open_shares_one_started_session(tmp_path, monkeypatch):
    path = tmp_path / "shared.ipynb"
    path.write_text('{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}')
    manager = NotebookWorkspace(NotebookWorkspaceConfig(mode="local"))
    started = asyncio.Event()
    release = asyncio.Event()
    sessions = []

    class StubSession:
        def __init__(self):
            self.stop_calls = 0

        async def start(self):
            started.set()
            await release.wait()

        async def stop(self):
            self.stop_calls += 1

    def build(_path):
        session = StubSession()
        sessions.append(session)
        return session

    monkeypatch.setattr(manager, "_build_session", build)
    first = asyncio.create_task(manager.open(str(path)))
    await started.wait()
    second = asyncio.create_task(manager.open(str(path.parent / "." / path.name)))
    await asyncio.sleep(0)
    release.set()

    first_session, second_session = await asyncio.gather(first, second)
    assert first_session is second_session
    assert len(sessions) == 1
    assert len(manager) == 1

    assert await manager.close(str(path)) is True
    assert sessions[0].stop_calls == 1


@pytest.mark.asyncio
async def test_open_waits_for_close_before_starting_replacement(monkeypatch):
    manager = NotebookWorkspace(NotebookWorkspaceConfig(mode="server"))
    stop_started = asyncio.Event()
    finish_stop = asyncio.Event()
    sessions = []

    class StubSession:
        def __init__(self):
            self.alive = False

        async def start(self):
            self.alive = True

        async def stop(self):
            stop_started.set()
            await finish_stop.wait()
            self.alive = False

    def build(_path):
        session = StubSession()
        sessions.append(session)
        return session

    monkeypatch.setattr(manager, "_build_session", build)
    original = await manager.open("race.ipynb")
    closing = asyncio.create_task(manager.close("race.ipynb"))
    await stop_started.wait()
    reopening = asyncio.create_task(manager.open("race.ipynb"))
    await asyncio.sleep(0)

    assert not reopening.done()
    assert len(sessions) == 1

    finish_stop.set()
    assert await closing is True
    replacement = await reopening
    assert replacement is not original
    assert replacement.alive is True
    assert original.alive is False
    assert len(sessions) == 2
    assert manager.get("race.ipynb") is replacement


@pytest.mark.asyncio
async def test_open_waits_until_delete_finishes(monkeypatch):
    manager = NotebookWorkspace(NotebookWorkspaceConfig(mode="server"))
    stop_started = asyncio.Event()
    finish_stop = asyncio.Event()
    delete_started = asyncio.Event()
    finish_delete = asyncio.Event()
    sessions = []

    class StubSession:
        async def start(self):
            pass

        async def stop(self):
            stop_started.set()
            await finish_stop.wait()

    def build(_path):
        session = StubSession()
        sessions.append(session)
        return session

    async def delete_file(_path):
        delete_started.set()
        await finish_delete.wait()
        return True

    monkeypatch.setattr(manager, "_build_session", build)
    monkeypatch.setattr(manager, "_delete_server_file", delete_file)
    await manager.open("delete-race.ipynb")
    deleting = asyncio.create_task(manager.delete("delete-race.ipynb"))
    await stop_started.wait()
    reopening = asyncio.create_task(manager.open("delete-race.ipynb"))

    finish_stop.set()
    await delete_started.wait()
    await asyncio.sleep(0)
    assert not reopening.done()
    assert len(sessions) == 1

    finish_delete.set()
    assert await deleting is True
    replacement = await reopening
    assert len(sessions) == 2
    assert manager.get("delete-race.ipynb") is replacement


@pytest.mark.asyncio
async def test_close_all_rejects_reopen_waiting_on_teardown(monkeypatch):
    manager = NotebookWorkspace(NotebookWorkspaceConfig(mode="server"))
    stop_started = asyncio.Event()
    finish_stop = asyncio.Event()
    sessions = []

    class StubSession:
        def __init__(self):
            self.alive = False

        async def start(self):
            self.alive = True

        async def stop(self):
            stop_started.set()
            await finish_stop.wait()
            self.alive = False

    def build(_path):
        session = StubSession()
        sessions.append(session)
        return session

    monkeypatch.setattr(manager, "_build_session", build)
    original = await manager.open("shutdown-race.ipynb")
    closing = asyncio.create_task(manager.close("shutdown-race.ipynb"))
    await stop_started.wait()

    reopening = asyncio.create_task(manager.open("shutdown-race.ipynb"))
    await asyncio.sleep(0)
    shutdown = asyncio.create_task(manager.close_all())
    await asyncio.sleep(0)
    finish_stop.set()

    assert await closing is True
    await shutdown
    with pytest.raises(RuntimeError, match="shutting down"):
        await reopening

    assert original.alive is False
    assert len(sessions) == 1
    assert len(manager) == 0
    with pytest.raises(RuntimeError, match="shutting down"):
        await manager.open("after-shutdown.ipynb")
