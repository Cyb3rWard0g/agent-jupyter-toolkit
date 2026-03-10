"""Tests for ContentsApiDocumentTransport without a live server."""

from __future__ import annotations

from copy import deepcopy

import pytest

from agent_jupyter_toolkit.notebook.transports.contents import ContentsApiDocumentTransport

pytestmark = pytest.mark.asyncio


def _notebook(cells: list[dict] | None = None) -> dict:
    return {
        "cells": cells or [],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


class _DummySession:
    closed = False

    async def close(self) -> None:
        self.closed = True


class FakeContentsTransport(ContentsApiDocumentTransport):
    def __init__(self, *, exists: bool = True, cells: list[dict] | None = None) -> None:
        super().__init__("http://example.test", "demo.ipynb")
        self._dummy_session = _DummySession()
        self._exists = exists
        self._content = _notebook(cells)
        self._version = 1

    async def start(self) -> None:
        self._session = self._dummy_session

    async def stop(self) -> None:
        self._session = None

    async def _json_request(self, method: str, url: str, **kwargs) -> dict:
        params = kwargs.get("params")
        if method == "GET" and params == {"content": "0", "type": "notebook"}:
            if not self._exists:
                raise RuntimeError(f"GET {url} failed (404): missing")
            return {"last_modified": f"ts-{self._version}"}
        if method == "GET":
            if not self._exists:
                raise RuntimeError(f"GET {url} failed (404): missing")
            return {
                "last_modified": f"ts-{self._version}",
                "content": deepcopy(self._content),
            }
        if method == "PUT":
            self._exists = True
            self._version += 1
            self._content = deepcopy(kwargs["json"]["content"])
            return {"last_modified": f"ts-{self._version}"}
        raise AssertionError(f"Unexpected request: {method} {url} {kwargs}")


async def test_contents_save_requires_fetch_baseline_for_existing_notebook():
    doc = FakeContentsTransport(exists=True)
    await doc.start()

    with pytest.raises(RuntimeError, match="Call fetch\\(\\) before save\\(\\)"):
        await doc.save(_notebook())


async def test_contents_save_allows_first_save_when_notebook_is_missing():
    doc = FakeContentsTransport(exists=False)
    await doc.start()

    await doc.save(
        _notebook([{"id": "a", "cell_type": "markdown", "metadata": {}, "source": "# new"}])
    )

    assert doc.last_modified == "ts-2"


async def test_contents_save_detects_external_modification_after_fetch():
    doc = FakeContentsTransport(exists=True)
    await doc.start()

    await doc.fetch()
    doc._version += 1

    with pytest.raises(RuntimeError, match="modified externally"):
        await doc.save(_notebook())


async def test_contents_move_cell_emits_consistent_event_payload():
    doc = FakeContentsTransport(
        exists=True,
        cells=[
            {
                "id": "a",
                "cell_type": "code",
                "metadata": {},
                "source": "a = 1",
                "outputs": [],
                "execution_count": None,
            },
            {"id": "b", "cell_type": "markdown", "metadata": {}, "source": "# middle"},
            {
                "id": "c",
                "cell_type": "code",
                "metadata": {},
                "source": "b = 2",
                "outputs": [],
                "execution_count": None,
            },
        ],
    )
    events: list[dict] = []
    doc.on_change(events.append)
    await doc.start()

    await doc.move_cell(0, 2)

    assert await doc.get_cell_source(0) == "# middle"
    assert events[-1] == {
        "op": "cells-mutated",
        "kind": "move",
        "index": 2,
        "from": 0,
        "to": 2,
    }
