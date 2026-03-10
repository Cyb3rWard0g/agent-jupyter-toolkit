"""Tests for NotebookSession.run_all using in-memory fakes."""

from __future__ import annotations

from copy import deepcopy

import pytest

from agent_jupyter_toolkit.kernel.types import ExecutionResult
from agent_jupyter_toolkit.notebook.session import NotebookSession

pytestmark = pytest.mark.asyncio


class FakeKernel:
    def __init__(self) -> None:
        self.started = False
        self.restart_calls = 0
        self.execution_count = 0

    async def start(self) -> None:
        self.started = True

    async def shutdown(self) -> None:
        self.started = False

    async def is_alive(self) -> bool:
        return self.started

    async def restart(self) -> None:
        self.restart_calls += 1
        self.execution_count = 0

    async def execute(
        self,
        code: str,
        *,
        on_output=None,
        on_exec_count=None,
        on_clear_output=None,
        output_callback=None,
        **kwargs,
    ) -> ExecutionResult:
        self.execution_count += 1
        outputs = [{"output_type": "stream", "name": "stdout", "text": code}]
        if on_exec_count:
            on_exec_count(self.execution_count)
        if on_output:
            on_output(outputs[0])
        if output_callback:
            await output_callback(outputs, self.execution_count)
        if "boom" in code:
            return ExecutionResult(
                status="error",
                execution_count=self.execution_count,
                stderr="boom",
                outputs=outputs,
            )
        return ExecutionResult(
            status="ok",
            execution_count=self.execution_count,
            stdout=code,
            outputs=outputs,
        )


class FakeDoc:
    def __init__(self, cells: list[dict]) -> None:
        self.started = False
        self.cells = deepcopy(cells)
        self.fetch_calls = 0

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def is_connected(self) -> bool:
        return self.started

    async def fetch(self) -> dict:
        self.fetch_calls += 1
        return {
            "cells": deepcopy(self.cells),
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }

    async def cell_count(self) -> int:
        return len(self.cells)

    async def get_cell(self, index: int) -> dict:
        return deepcopy(self.cells[index])

    async def update_cell_outputs(
        self,
        index: int,
        outputs: list[dict],
        execution_count: int | None,
    ) -> None:
        self.cells[index]["outputs"] = deepcopy(outputs)
        self.cells[index]["execution_count"] = execution_count


async def test_run_all_tracks_skipped_cells_in_notebook_order():
    kernel = FakeKernel()
    doc = FakeDoc(
        [
            {"id": "m1", "cell_type": "markdown", "metadata": {}, "source": "# title"},
            {
                "id": "c0",
                "cell_type": "code",
                "metadata": {},
                "source": "   ",
                "outputs": [],
                "execution_count": None,
            },
            {
                "id": "c1",
                "cell_type": "code",
                "metadata": {},
                "source": "x = 1",
                "outputs": [],
                "execution_count": None,
            },
            {
                "id": "c2",
                "cell_type": "code",
                "metadata": {},
                "source": "boom()",
                "outputs": [],
                "execution_count": None,
            },
            {
                "id": "c3",
                "cell_type": "code",
                "metadata": {},
                "source": "y = 2",
                "outputs": [],
                "execution_count": None,
            },
        ]
    )
    session = NotebookSession(kernel=kernel, doc=doc)

    result = await session.run_all(stop_on_error=False)
    await session.stop()

    assert result.status == "error"
    assert result.executed_count == 3
    assert result.skipped_count == 2
    assert [cell.status for cell in result.cells] == ["skipped", "skipped", "ok", "error", "ok"]
    assert result.first_failure is not None
    assert result.first_failure.index == 3
    assert doc.fetch_calls == 1


async def test_restart_and_run_all_restarts_before_execution():
    kernel = FakeKernel()
    doc = FakeDoc(
        [
            {
                "id": "c1",
                "cell_type": "code",
                "metadata": {},
                "source": "x = 1",
                "outputs": [],
                "execution_count": None,
            },
        ]
    )
    session = NotebookSession(kernel=kernel, doc=doc)

    result = await session.restart_and_run_all()
    await session.stop()

    assert result.status == "ok"
    assert result.executed_count == 1
    assert kernel.restart_calls == 1
