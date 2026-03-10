"""
Remote Jupyter server notebook transport via Contents API.

This module provides the ContentsApiDocumentTransport class for reading and writing
Jupyter notebooks from remote Jupyter servers using the Contents API. This transport
enables working with notebooks hosted on Jupyter servers, including JupyterLab and
Jupyter Notebook instances.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import aiohttp

from ..transport import NotebookDocumentTransport
from ..utils import create_notebook_via_contents_api

log = logging.getLogger(__name__)


class ContentsApiDocumentTransport(NotebookDocumentTransport):
    """
    Remote notebook read/write via the Jupyter Contents API.

    This transport communicates with a remote Jupyter server using the standard
    Contents API endpoints for notebook management. It provides full notebook
    persistence and manipulation capabilities over HTTP.

    API Operations:
        - fetch(): GET /api/contents/<path> → returns notebook model with content
        - save():  PUT /api/contents/<path> with {"type":"notebook","format":"json","content": ...}
        - cell ops: serialized (fetch → mutate → save) behind an internal asyncio.Lock

    Key Features:
        - Thread-safe serialization of all mutations via internal locking
        - Automatic notebook creation if requested via create_if_missing parameter
        - Configurable request timeouts and authentication
        - Full nbformat compatibility and validation
        - Change event notifications for external listeners

    Authentication:
        Supports multiple authentication methods:
        - Token-based: pass `token` parameter for "Authorization: Token <token>" header
        - Cookie-based: include session cookies in `headers` parameter
        - Custom headers: pass any additional headers for reverse proxies, XSRF, etc.

    Example:
        ```python
        transport = ContentsApiDocumentTransport(
            base_url="http://localhost:8888",
            path="notebooks/analysis.ipynb",
            token="your-jupyter-token",
            create_if_missing=True,
            request_timeout=60.0
        )

        await transport.start()  # Open HTTP session

        # Fetch current notebook content
        content = await transport.fetch()

        # Add cells
        await transport.append_markdown_cell("# Data Analysis")
        code_idx = await transport.append_code_cell("import pandas as pd")

        # Update with execution results
        await transport.update_cell_outputs(code_idx, outputs, execution_count=1)

        await transport.stop()  # Close HTTP session
        ```

    Note:
        This transport does NOT execute code - it only manages notebook document
        structure and persistence. Use a separate Kernel/Session for code execution.
    """

    def __init__(
        self,
        base_url: str,
        path: str,
        token: str | None = None,
        headers: dict[str, str] | None = None,
        *,
        create_if_missing: bool = False,
        request_timeout: float = 30.0,
    ) -> None:
        """
        Initialize the Contents API transport.

        Args:
            base_url: Jupyter Server base URL (e.g. "http://localhost:8888")
            path: Notebook path relative to server root (e.g. "analysis.ipynb" or
                "notebooks/analysis.ipynb")
            token: Optional API token (adds "Authorization: Token <token>" header)
            headers: Optional extra headers (cookies, XSRF tokens, reverse-proxy headers, etc.)
            create_if_missing: If True, creates a minimal notebook if GET returns 404 on start()
            request_timeout: Per-request timeout in seconds for all HTTP operations

        Note:
            The transport maintains a persistent HTTP session for efficiency.
            All notebook mutations are serialized internally to prevent race conditions.
        """
        self._base = base_url.rstrip("/")
        self._path = path

        self._http_headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **(headers or {}),
        }
        if token:
            self._http_headers["Authorization"] = f"Token {token}"

        self._session: aiohttp.ClientSession | None = None
        self._on_change: list[Callable[[dict[str, Any]], None]] = []
        self._lock: asyncio.Lock = asyncio.Lock()
        self._create_if_missing = bool(create_if_missing)
        self._timeout = float(request_timeout)

        # Stale-write guard: tracks the server ``last_modified`` timestamp
        # from the most recent fetch so that mutations can detect concurrent
        # external edits before saving.
        self._last_modified: str | None = None
        self._has_freshness_baseline = False

    async def start(self) -> None:
        """
        Open a reusable HTTP session. Optionally ensure the notebook file exists.
        Idempotent.
        """
        # Early return if already connected to prevent duplicate operations
        if self._session is not None and not self._session.closed:
            return

        if self._session is None:
            self._session = aiohttp.ClientSession(headers=self._http_headers)

        if self._create_if_missing:
            log.info("Ensuring notebook exists at %s", self._path)
            try:
                # Use the utility function that properly checks existence AND creates if needed
                created = await create_notebook_via_contents_api(
                    base_url=self._base,
                    path=self._path,
                    headers=self._http_headers,
                    check_exists=True,
                    timeout=self._timeout,
                )
                if created:
                    log.info("Notebook created successfully at %s", self._path)
            except Exception as e:
                log.error("Failed to ensure notebook exists: %s", e)
                raise RuntimeError(f"Could not create/verify notebook {self._path}: {e}") from e

    async def stop(self) -> None:
        """Close the HTTP session."""
        if self._session:
            try:
                await self._session.close()
            finally:
                self._session = None

    async def is_connected(self) -> bool:
        """True if session is open."""
        return self._session is not None and not self._session.closed

    async def fetch(self) -> dict[str, Any]:
        """
        Return nbformat-like notebook dict (content only).

        As a side-effect, the server's ``last_modified`` timestamp is stored
        so that subsequent mutations can detect concurrent external edits
        (see :pymethod:`_check_stale`).

        Raises:
            RuntimeError with server response details if fetch fails.
        """
        assert self._session is not None, "Call start() first"
        url = f"{self._base}/api/contents/{quote(self._path)}"
        model = await self._json_request("GET", url)
        # Store last_modified for stale-write detection
        self._last_modified = model.get("last_modified")
        self._has_freshness_baseline = self._last_modified is not None
        content = model.get("content") or {}
        if not isinstance(content, dict):
            raise RuntimeError(f"Contents GET returned unexpected content for {self._path}")
        return content

    async def get_cell(self, index: int) -> dict[str, Any]:
        """Return the cell at *index* via the Contents API.

        Fetches the full notebook and extracts the requested cell.
        The Jupyter Contents API does not support cell-level reads, so
        a full ``fetch()`` is unavoidable here.
        """
        content = await self.fetch()
        cells = content.get("cells") or []
        if index < 0 or index >= len(cells):
            raise IndexError(f"get_cell: index {index} out of range 0..{len(cells) - 1}")
        return cells[index]

    async def cell_count(self) -> int:
        """Return the number of cells via the Contents API."""
        content = await self.fetch()
        return len(content.get("cells") or [])

    async def get_cell_source(self, index: int) -> str:
        """Return the source text of the cell at *index*."""
        cell = await self.get_cell(index)
        return cell.get("source", "")

    async def save(self, content: dict[str, Any]) -> None:
        """PUT the entire notebook content (content must be an nbformat-like dict).

        Before every save, the server is queried first (metadata-only GET) to
        verify the notebook has not been modified externally. For existing
        notebooks, callers must establish a freshness baseline with ``fetch()``
        before the first save so the transport can detect stale writes instead
        of silently overwriting newer content.

        Raises:
            RuntimeError: if the notebook was modified externally since the
                last fetch, if no freshness baseline has been established for
                an existing notebook, or on any HTTP error.
        """
        assert self._session is not None, "Call start() first"
        await self._check_stale()
        url = f"{self._base}/api/contents/{quote(self._path)}"
        body = {"type": "notebook", "format": "json", "content": content}
        model = await self._json_request("PUT", url, json=body)
        # Update tracked timestamp from the server response after a
        # successful save so the next mutation uses the fresh value.
        if isinstance(model, dict) and "last_modified" in model:
            self._last_modified = model["last_modified"]
            self._has_freshness_baseline = self._last_modified is not None
        for cb in self._on_change:
            cb({"op": "save"})

    async def _mutate_cells(
        self,
        mutator,
        *,
        kind: str,
        event: dict[str, Any] | None = None,
    ) -> int:
        """
        Serialize: fetch → mutate → save.

        `mutator(cells)` must mutate the list in-place and return the target index.
        Emits an on_change event with the given `kind`.
        """
        async with self._lock:
            content = await self.fetch()
            cells = content.get("cells") or []
            if not isinstance(cells, list):
                # normalize if server returns something unexpected
                cells = []
            index = mutator(cells)
            content["cells"] = cells
            await self.save(content)
            log.debug("Contents mutation '%s' at index %s", kind, index)
            payload = {"op": "cells-mutated", "kind": kind, "index": index}
            if event:
                payload.update(event)
            for cb in self._on_change:
                cb(payload)
            return index

    async def append_code_cell(
        self,
        source: str,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> int:
        """Append a code cell and return its new index."""

        def m(cells):
            cell = {
                "id": uuid.uuid4().hex,
                "cell_type": "code",
                "metadata": dict(metadata or {}),
                "source": source,
                "outputs": [],
                "execution_count": None,
            }
            if tags is not None:
                _validate_tags(tags)
                cell["metadata"].setdefault("tags", [])
                cell["metadata"]["tags"] = list(set(cell["metadata"]["tags"]).union(tags))
            cells.append(cell)
            return len(cells) - 1

        return await self._mutate_cells(m, kind="append_code")

    async def insert_code_cell(
        self,
        index: int,
        source: str,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Insert a code cell at a given index (0..len)."""

        def m(cells):
            if index < 0 or index > len(cells):
                raise IndexError(f"insert_code_cell: index {index} out of range 0..{len(cells)}")
            cell = {
                "id": uuid.uuid4().hex,
                "cell_type": "code",
                "metadata": dict(metadata or {}),
                "source": source,
                "outputs": [],
                "execution_count": None,
            }
            if tags is not None:
                _validate_tags(tags)
                cell["metadata"].setdefault("tags", [])
                cell["metadata"]["tags"] = list(set(cell["metadata"]["tags"]).union(tags))
            cells.insert(index, cell)
            return index

        await self._mutate_cells(m, kind="insert_code")

    async def update_cell_outputs(
        self,
        index: int,
        outputs: list[dict[str, Any]],
        execution_count: int | None,
    ) -> None:
        """Replace outputs (and execution_count) of the code cell at `index`."""

        def m(cells):
            if index < 0 or index >= len(cells):
                raise IndexError(
                    f"update_cell_outputs: index {index} out of range 0..{len(cells) - 1}"
                )
            cell = cells[index]
            if not isinstance(outputs, list) or not all(isinstance(o, dict) for o in outputs):
                raise TypeError("update_cell_outputs: 'outputs' must be a list of dicts")
            cell["outputs"] = outputs
            cell["execution_count"] = execution_count
            return index

        await self._mutate_cells(m, kind="outputs")

    async def append_markdown_cell(self, source: str, tags: list[str] | None = None) -> int:
        """Append a markdown cell and return its new index."""

        def m(cells):
            cell = {
                "id": uuid.uuid4().hex,
                "cell_type": "markdown",
                "metadata": {},
                "source": source,
            }
            if tags is not None:
                _validate_tags(tags)
                cell["metadata"].setdefault("tags", [])
                cell["metadata"]["tags"] = list(set(cell["metadata"]["tags"]).union(tags))
            cells.append(cell)
            return len(cells) - 1

        return await self._mutate_cells(m, kind="append_markdown")

    async def insert_markdown_cell(
        self, index: int, source: str, tags: list[str] | None = None
    ) -> None:
        """Insert a markdown cell at a given index (0..len)."""

        def m(cells):
            if index < 0 or index > len(cells):
                raise IndexError(
                    f"insert_markdown_cell: index {index} out of range 0..{len(cells)}"
                )
            cell = {
                "id": uuid.uuid4().hex,
                "cell_type": "markdown",
                "metadata": {},
                "source": source,
            }
            if tags is not None:
                _validate_tags(tags)
                cell["metadata"].setdefault("tags", [])
                cell["metadata"]["tags"] = list(set(cell["metadata"]["tags"]).union(tags))
            cells.insert(index, cell)
            return index

        await self._mutate_cells(m, kind="insert_markdown")

    async def set_cell_source(self, index: int, source: str) -> None:
        """Replace the source text for the cell at `index` (code or markdown)."""

        def m(cells):
            if index < 0 or index >= len(cells):
                raise IndexError(f"set_cell_source: index {index} out of range 0..{len(cells) - 1}")
            cells[index]["source"] = source
            return index

        await self._mutate_cells(m, kind="set_source")

    async def delete_cell(self, index: int) -> None:
        """Delete the cell at `index`."""

        def m(cells):
            if index < 0 or index >= len(cells):
                raise IndexError(f"delete_cell: index {index} out of range 0..{len(cells) - 1}")
            del cells[index]
            return index

        await self._mutate_cells(m, kind="delete")

    # ---------- cell addressing by ID ----------

    async def resolve_cell_index(self, cell_id: str) -> int:
        """Return the zero-based index of the cell matching *cell_id*.

        Fetches the notebook and performs a linear scan over cells.  The
        Contents API has no cell-level addressing so a full fetch is required.

        Raises:
            KeyError: if no cell with the given ID exists.
        """
        content = await self.fetch()
        cells = content.get("cells") or []
        for idx, cell in enumerate(cells):
            if cell.get("id") == cell_id:
                return idx
        raise KeyError(f"No cell with id {cell_id!r}")

    async def get_cell_by_id(self, cell_id: str) -> dict[str, Any]:
        """Return the cell whose ``id`` matches *cell_id*.

        Raises:
            KeyError: if no cell with the given ID exists.
        """
        content = await self.fetch()
        cells = content.get("cells") or []
        for cell in cells:
            if cell.get("id") == cell_id:
                return cell
        raise KeyError(f"No cell with id {cell_id!r}")

    # ---------- cell reordering ----------

    async def move_cell(self, from_index: int, to_index: int) -> None:
        """Move the cell at *from_index* to *to_index*.

        The operation is serialized behind the internal mutation lock so it
        is safe from concurrent modification.  Emits a ``cells-mutated``
        change event with ``kind="move"``.

        Raises:
            IndexError: if either index is out of range ``[0..len-1]``.
        """

        def m(cells):
            n = len(cells)
            if from_index < 0 or from_index >= n:
                raise IndexError(f"move_cell: from_index {from_index} out of range 0..{n - 1}")
            if to_index < 0 or to_index >= n:
                raise IndexError(f"move_cell: to_index {to_index} out of range 0..{n - 1}")
            if from_index != to_index:
                cell = cells.pop(from_index)
                cells.insert(to_index, cell)
            return to_index

        await self._mutate_cells(
            m,
            kind="move",
            event={"from": from_index, "to": to_index},
        )

    def on_change(self, cb: Callable[[dict[str, Any]], None]) -> None:
        """Register a callback invoked after save/mutation."""
        self._on_change.append(cb)

    # ---------- stale-write guard ----------

    @property
    def last_modified(self) -> str | None:
        """The ``last_modified`` timestamp from the most recent ``fetch()``.

        Returns ``None`` if no fetch has been performed yet.  This value is
        automatically updated after every successful ``fetch()`` and ``save()``.
        """
        return self._last_modified

    async def _check_stale(self) -> None:
        """Verify the notebook has not been modified externally.

        Performs a lightweight metadata-only GET (``content=0``) against the
        Contents API and compares the server's ``last_modified`` value to the
        one recorded by the most recent ``fetch()``.

        Raises:
            RuntimeError: if the server's ``last_modified`` differs from the
                tracked value, indicating an external concurrent edit.
        """
        model = await self._fetch_metadata(allow_missing=True)
        if model is None:
            return
        server_ts = model.get("last_modified")
        if not self._has_freshness_baseline:
            raise RuntimeError(
                f"Refusing to save notebook {self._path!r} without a freshness baseline. "
                f"Call fetch() before save() so concurrent edits can be detected."
            )
        if server_ts and server_ts != self._last_modified:
            raise RuntimeError(
                f"Notebook {self._path!r} was modified externally "
                f"(expected last_modified={self._last_modified!r}, "
                f"server has {server_ts!r}). "
                f"Re-fetch the notebook before saving."
            )

    async def _fetch_metadata(self, *, allow_missing: bool = False) -> dict[str, Any] | None:
        """Fetch notebook metadata without content.

        Returns ``None`` when ``allow_missing`` is true and the notebook does
        not yet exist on the server.
        """
        assert self._session is not None, "Call start() first"
        url = f"{self._base}/api/contents/{quote(self._path)}"
        try:
            return await self._json_request("GET", url, params={"content": "0", "type": "notebook"})
        except RuntimeError as exc:
            if allow_missing and "failed (404)" in str(exc):
                return None
            raise

    async def _json_request(self, method: str, url: str, **kwargs) -> dict[str, Any]:
        """
        Perform an HTTP request and return JSON (or {} if no JSON).
        Raises RuntimeError with response text on non-2xx.
        """
        assert self._session is not None, "Call start() first"
        try:
            async with self._session.request(method, url, timeout=self._timeout, **kwargs) as r:
                if 200 <= r.status < 300:
                    # Some 204 operations have empty bodies; guard decode
                    if r.content_type and "application/json" in r.content_type.lower():
                        return await r.json()
                    return {}
                text = await r.text()
                raise RuntimeError(f"{method} {url} failed ({r.status}): {text}")
        except TimeoutError as err:
            raise RuntimeError(f"{method} {url} timed out after {self._timeout}s") from err


def _validate_tags(tags: list[str]) -> None:
    """Ensure tags is a list of strings (raise TypeError otherwise)."""
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise TypeError("tags must be a list of strings")
