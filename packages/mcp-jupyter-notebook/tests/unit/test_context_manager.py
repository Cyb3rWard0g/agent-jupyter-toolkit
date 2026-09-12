"""Concurrency tests for the MCP notebook session registry."""

from __future__ import annotations

import asyncio

import pytest

from mcp_jupyter_notebook.context import SessionManager


@pytest.mark.asyncio
async def test_concurrent_open_shares_one_started_session(tmp_path, monkeypatch):
    path = tmp_path / "shared.ipynb"
    path.write_text('{"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}')
    manager = SessionManager({"mode": "local"})
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
    manager = SessionManager({"mode": "server"})
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
    manager = SessionManager({"mode": "server"})
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
    manager = SessionManager({"mode": "server"})
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
