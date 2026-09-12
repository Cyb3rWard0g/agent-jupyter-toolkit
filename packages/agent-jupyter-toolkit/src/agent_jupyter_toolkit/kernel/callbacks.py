"""Bounded delivery of coalesced execution-output callbacks."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

from .types import ExecutionResult, OutputCallback


class OutputCallbackDispatcher:
    """Deliver the newest execution snapshot without blocking message intake."""

    def __init__(
        self,
        callback: OutputCallback | None,
        snapshot: Callable[[], tuple[list[dict], int | None]],
        *,
        timeout: float | None,
    ) -> None:
        self._callback = callback
        self._snapshot = snapshot
        self._timeout = timeout
        self._event = asyncio.Event()
        self._closing = False
        self._version = 0
        self._error: BaseException | None = None
        self.coalesced = 0
        self._task = (
            asyncio.create_task(self._consume(), name="ajt-output-callback")
            if callback is not None
            else None
        )

    def publish(self) -> None:
        """Schedule the latest state, replacing an older pending snapshot."""
        if self._task is None or self._task.done():
            return
        self._version += 1
        if self._event.is_set():
            self.coalesced += 1
        self._event.set()

    async def finish(self) -> BaseException | None:
        """Deliver a final snapshot and wait for the consumer to settle."""
        if self._task is None:
            return None
        self._closing = True
        self.publish()
        await self._task
        return self._error

    async def cancel(self) -> None:
        """Cancel an active callback when its owning execution is cancelled."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    def apply_to(self, result: ExecutionResult) -> None:
        """Copy callback delivery diagnostics to an execution result."""
        result.callback_snapshots_coalesced = self.coalesced
        if self._callback is None:
            result.callback_status = "not-requested"
        elif self._error is None:
            result.callback_status = "ok"
        else:
            result.callback_status = "error"
            result.callback_error = f"{type(self._error).__name__}: {self._error}"

    async def _consume(self) -> None:
        assert self._callback is not None
        while True:
            await self._event.wait()
            self._event.clear()
            delivered_version = self._version
            try:
                callback = self._callback(*self._snapshot())
                if self._timeout is None:
                    await callback
                else:
                    await asyncio.wait_for(callback, timeout=self._timeout)
            except asyncio.CancelledError as exc:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
                self._error = exc
                return
            except BaseException as exc:
                self._error = exc
                return
            if self._closing and delivered_version == self._version and not self._event.is_set():
                return
