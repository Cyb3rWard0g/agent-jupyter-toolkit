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
