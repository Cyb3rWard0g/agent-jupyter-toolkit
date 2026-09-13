"""Explicit collaboration-to-Contents fallback transport."""

from __future__ import annotations

import contextlib
from typing import Any

import aiohttp


class CollaborationFallbackTransport:
    """Prefer collaboration and fall back only when its server API is unsupported."""

    def __init__(self, collaboration: Any, contents: Any) -> None:
        self._collaboration = collaboration
        self._contents = contents
        self._active = collaboration
        self.fallback_reason: str | None = None
        self.collaboration_mode = "preferred"
        self._started = False

    @property
    def selected_transport(self) -> str:
        return "collaboration" if self._active is self._collaboration else "contents"

    async def start(self) -> None:
        if self._started:
            return
        self._active = self._collaboration
        self.fallback_reason = None
        try:
            await self._collaboration.start()
        except aiohttp.ClientResponseError as exc:
            if exc.status not in {400, 404, 405, 426, 501}:
                raise
            self.fallback_reason = f"collaboration API returned HTTP {exc.status}"
            with contextlib.suppress(Exception):
                await self._collaboration.stop()
            self._active = self._contents
            await self._contents.start()
        self._started = True

    async def stop(self) -> None:
        try:
            await self._active.stop()
        finally:
            self._started = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._active, name)
