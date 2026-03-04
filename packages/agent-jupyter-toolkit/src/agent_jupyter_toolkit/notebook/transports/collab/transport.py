"""
Collaborative notebook transport using Yjs and CRDT.

This module provides the CollabYjsDocumentTransport class for real-time collaborative
editing of Jupyter notebooks using Yjs (Y.js) and Conflict-free Replicated Data Types
(CRDT). This transport enables multiple users to simultaneously edit the same notebook
with automatic conflict resolution and real-time synchronization.

Key features:
- Real-time collaborative editing with automatic conflict resolution
- CRDT-based document synchronization for consistency across clients
- WebSocket-based communication with Jupyter collaboration servers
- Compatible with JupyterLab's native collaborative editing features

Note: This transport requires a Jupyter server with collaboration features enabled.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import aiohttp
import pycrdt
from jupyter_ydoc import YNotebook

from ...transport import NotebookDocumentTransport
from ...utils import create_notebook_via_contents_api
from .protocol import hex_preview, looks_like_yws, safe_handle_sync_message
from .yutils import (
    make_code_cell_dict,
    make_md_cell_dict,
    validate_tags,
    ytext_to_str,
)

if TYPE_CHECKING:
    from pycrdt import Array as YArray
    from pycrdt import Awareness
    from pycrdt import Doc as YDoc
    from pycrdt import Map as YMap

# stable logger names regardless of package shape
BASE_LOGGER = "agent_jupyter_toolkit.notebook.transports.collab"
log = logging.getLogger(BASE_LOGGER)
wslog = logging.getLogger(BASE_LOGGER + ".ws")

create_awareness_message = pycrdt.create_awareness_message
create_sync_message = pycrdt.create_sync_message
create_update_message = pycrdt.create_update_message


class CollabYjsDocumentTransport(NotebookDocumentTransport):
    """
    Jupyter Collaboration (Yjs) notebook transport for real-time collaborative editing.

    This transport connects to a Jupyter server's Collaboration API and synchronizes a
    shared Yjs document (YDoc) for a notebook. Multiple users can edit the same notebook
    simultaneously with real-time conflict resolution using Conflict-free Replicated
    Data Types (CRDTs).

    Architecture:
        - Uses Jupyter server's /api/collaboration endpoints
        - Maintains WebSocket connection to collaboration room
        - Local YDoc mirrors the server-side document state
        - All mutations are applied as Y transactions and broadcast
        - Compatible with JupyterLab's collaborative editing features

    Server endpoints:
        - PUT /api/collaboration/session/<path> → { sessionId, fileId, type, format }
        - WS  /api/collaboration/room/<format:type:fileId>?sessionId=... [&token=...]

    Local YDoc schema (stored under map "notebook"):
        notebook: YMap
            - "cells": YArray of YMap (each cell)
                cell:
                  - id: str
                  - cell_type: "code" | "markdown"
                  - metadata: YMap
                  - source: YText
                  - (code) outputs: YArray
                  - (code) execution_count: int | None

    Dependencies:
        - pycrdt: Python CRDT library for Yjs interoperability
        - jupyter_ydoc: Jupyter-specific Yjs document schemas
        - aiohttp: HTTP client for WebSocket and REST communication

    Example:
        ```python
        transport = CollabYjsDocumentTransport(
            base_url="http://localhost:8888",
            path="shared_notebook.ipynb",
            token="your-token",
            create_if_missing=True
        )

        await transport.start()  # Join collaboration room

        # Add content that will be visible to all collaborators
        await transport.append_markdown_cell("# Shared Analysis")
        idx = await transport.append_code_cell("import pandas as pd")

        await transport.stop()  # Leave collaboration room
        ```

    Note:
        This transport requires a Jupyter server with collaboration features enabled.
        The notebook will be created automatically if `create_if_missing=True` and
        it doesn't exist on the server.
    """

    def __init__(
        self,
        base_url: str,
        path: str,
        *,
        token: str | None = None,
        headers: dict[str, str] | None = None,
        username: str = "agent",
        heartbeat: int = 30,
        create_if_missing: bool = False,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._path = path
        self._token = token
        self._headers = headers or {}
        self._http_headers: dict[str, str] = {
            "Accept": "application/json",
            **self._headers,
        }
        if token:
            self._http_headers["Authorization"] = f"Token {token}"

        self._username = username
        self._heartbeat = int(heartbeat)
        self._create_if_missing = create_if_missing

        # HTTP/WS resources
        self._http: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._pump: asyncio.Task | None = None

        # Collaboration session ids
        self._room_id: str | None = None
        self._session_id: str | None = None

        # Local Y model (created in start())
        self._doc: YDoc | None = None
        self._awareness: Awareness | None = None
        self._root: YMap | None = None
        self._cells: YArray | None = None

        # YNotebook (Lab-compatible schema)
        self._ynb: YNotebook | None = None

        # Local mutation serialization
        self._op_lock = asyncio.Lock()
        self._on_change: list[Callable[[dict[str, Any]], None]] = []
        self._on_awareness_change: list[Callable[[dict[int, dict[str, Any]]], None]] = []
        self._awareness_observer = None
        self._awareness_ping: asyncio.Task | None = None

        # Transaction completion tracking
        self._pending_updates: dict[int, asyncio.Future] = {}
        self._update_counter = 0

        # Yjs sync barriers
        self._initial_sync_done: asyncio.Event = asyncio.Event()
        self._initial_sync_timeout: float = 2.0

        # Bootstrap when server file content (default empty cell) is visible
        self._cells_bootstrapped: asyncio.Event = asyncio.Event()
        self._cells_bootstrap_timeout: float = 1.0

    async def start(self) -> None:
        """
        Connect HTTP + room WS, run Yjs handshake, and ensure the root schema exists.
        Idempotent: returns early if already connected.
        """
        if (
            self._ws
            and not self._ws.closed
            and self._http
            and not self._http.closed
            and self._room_id is not None
            and self._session_id is not None
        ):
            wslog.debug("Collaborative transport already fully started, skipping")
            return

        # Reset barriers for a fresh start
        if self._initial_sync_done.is_set():
            self._initial_sync_done.clear()
        if self._cells_bootstrapped.is_set():
            self._cells_bootstrapped.clear()

        # HTTP session
        if self._http is None or self._http.closed:
            if self._http and not self._http.closed:
                await self._http.close()
            self._http = aiohttp.ClientSession(headers=self._http_headers)

        # Ensure notebook exists
        if self._create_if_missing:
            wslog.info("Ensuring notebook exists before collaboration: %s", self._path)
            try:
                created = await create_notebook_via_contents_api(
                    base_url=self._base,
                    path=self._path,
                    headers=self._http_headers,
                    check_exists=True,
                    timeout=5.0,
                )
                if created:
                    wslog.info("Notebook created successfully via Contents API: %s", self._path)
            except Exception as e:
                wslog.error("Failed to ensure notebook exists: %s", e)
                raise RuntimeError(f"Could not create/verify notebook {self._path}: {e}") from e

        # Local Y doc
        if self._doc is None:
            self._doc = pycrdt.Doc()
        if self._awareness is None:
            self._awareness = pycrdt.Awareness(self._doc)
            self._awareness.set_local_state({"user": {"name": self._username}})
            self._setup_awareness_callbacks()
            self._start_awareness_ping()

        # Session (creates if missing)
        if self._create_if_missing:
            await asyncio.sleep(5)
        # Session creation request
        url = f"{self._base}/api/collaboration/session/{quote(self._path)}"
        async with self._http.put(url, json={"format": "json", "type": "notebook"}) as r:
            r.raise_for_status()
            info = await r.json()
        # Parse response
        fmt = info.get("format", "json")
        typ = info.get("type", "notebook")
        file_id = info.get("fileId")
        self._session_id = info.get("sessionId")
        # Validate response fields we need to proceed with collaboration
        if not (file_id and self._session_id):
            raise RuntimeError(f"Invalid collab session response: {info}")
        # Define room id (e.g. "json:notebook:abc123")
        self._room_id = f"{fmt}:{typ}:{file_id}"
        wslog.info(
            "Collaboration session created: room=%s, session=%s",
            self._room_id,
            self._session_id,
        )

        # Join WS and start pump
        ws_base = self._base.replace("https://", "wss://").replace("http://", "ws://")
        qs = f"sessionId={self._session_id}"
        if self._token:
            qs += f"&token={self._token}"
        ws_url = f"{ws_base}/api/collaboration/room/{self._room_id}?{qs}"
        self._ws = await self._http.ws_connect(ws_url, heartbeat=self._heartbeat)
        # Start WebSocket pump
        self._pump = asyncio.create_task(self._pump_ws(), name="jat-yjs-pump")
        wslog.info("Joined collaboration room %s", self._room_id)

        # SYNC handshake (pycrdt builds full frame)
        doc = self._doc
        assert doc is not None
        # Announce presence + SYNC
        await self._send_awareness()

        try:
            async with self._op_lock:
                sync_msg = create_sync_message(doc)  # full y-ws frame
            if sync_msg:
                await self._ws.send_bytes(sync_msg)
                wslog.debug("Sent SYNC_STEP1 (pycrdt framed)")
            else:
                wslog.debug("No initial sync message needed")
        except Exception as e:
            wslog.warning("Initial sync failed: %s", e)

        # Wait for first applied frame
        try:
            await asyncio.wait_for(
                self._initial_sync_done.wait(), timeout=self._initial_sync_timeout
            )
            wslog.debug("Initial Yjs sync barrier passed (ydoc has remote state)")
        except TimeoutError:
            wslog.debug(
                "Initial Yjs sync barrier timed out after %.2fs; continuing best-effort",
                self._initial_sync_timeout,
            )

        self._log_cells_snapshot("post-barrier (before _ensure_root)")

        # Initialize YNotebook
        if self._ynb is None:
            self._ynb = YNotebook(ydoc=self._doc, awareness=self._awareness)

        # Ensure root schema
        await self._ensure_root()
        self._log_cells_snapshot("post-ensure-root")

        # Fast-path: the initial sync may have delivered cells before _ynb
        # existed, so the pump's bootstrap check was skipped.  Re-check now.
        if not self._cells_bootstrapped.is_set() and self._ynb is not None:
            try:
                with self._doc.transaction():
                    cells = self._ynb.ycells
                    if isinstance(cells, pycrdt.Array) and len(cells) >= 1:
                        self._cells_bootstrapped.set()
                        wslog.debug(
                            "Cells already present after ensure_root (len=%d); "
                            "bootstrap fast-path triggered",
                            len(cells),
                        )
            except Exception:
                pass

        # Brief wait for server's default empty cell to land
        if not self._cells_bootstrapped.is_set():
            try:
                await asyncio.wait_for(
                    self._cells_bootstrapped.wait(),
                    timeout=self._cells_bootstrap_timeout,
                )
                wslog.debug("Cells bootstrap barrier passed (initial cells present)")
            except TimeoutError:
                wslog.debug("Cells bootstrap barrier timed out; proceeding best-effort")

        # Strip the server's default empty code cell so the notebook starts
        # truly empty from the session's perspective.  jupyter_ydoc's
        # YNotebook._set() always injects one blank code cell when the
        # notebook file has an empty cells array; leaving it around shifts
        # every index by 1 and confuses downstream tools.
        await self._strip_default_empty_cell()

    async def _strip_default_empty_cell(self) -> None:
        """Strip the server-injected default empty code cell.

        ``jupyter_ydoc``'s ``YNotebook._set()`` injects a single empty code
        cell whenever a notebook is loaded with an empty ``cells`` list.
        This helper attempts to remove it so the notebook starts truly empty.

        It tries immediately and, if the cells array is still empty (the
        server hasn't delivered the cell yet), waits briefly and retries once.
        """
        doc = self._doc
        if doc is None or self._ynb is None:
            return

        for attempt in range(2):
            try:
                with doc.transaction():
                    cells = self._ynb.ycells
                    if not isinstance(cells, pycrdt.Array):
                        return
                    n = len(cells)
                    if n == 1:
                        ycell = cells[0]
                        cell_type = ycell.get("cell_type")
                        src = ytext_to_str(ycell.get("source"))
                        if cell_type == "code" and src.strip() == "":
                            del cells[0]
                            wslog.debug(
                                "Stripped server's default empty code cell "
                                "(attempt %d); notebook now has %d cells",
                                attempt + 1,
                                len(cells),
                            )
                            break  # success
                        return  # cell has real content; leave it alone
                    elif n > 1:
                        return  # multiple cells; not a fresh notebook
                    # n == 0: cell hasn't landed yet; retry after a brief wait
            except Exception as exc:
                wslog.debug("Could not strip default empty cell: %s", exc)
                return

            if attempt == 0:
                await asyncio.sleep(self._cells_bootstrap_timeout)

        # Broadcast the deletion so the server persists it
        try:
            with doc.transaction():
                cells = self._ynb.ycells
            if isinstance(cells, pycrdt.Array) and len(cells) == 0:
                await self._broadcast_update()
                await self._wait_for_sync_completion()
        except Exception as exc:
            wslog.debug("Could not broadcast empty-cell strip: %s", exc)

    async def stop(self) -> None:
        """
        Close WebSocket and HTTP session; reset room/session IDs.
        Idempotent and tolerant to partially initialized states.
        """
        # Close WS first, so the pump stops receiving frames
        if self._ws and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception as e:
                wslog.debug("Error closing WebSocket: %s", e)
        self._ws = None

        # Now cancel and await the pump
        if self._pump and not self._pump.done():
            self._pump.cancel()
            try:
                await self._pump
            except asyncio.CancelledError:
                pass
            except Exception as e:
                wslog.debug("Error stopping WebSocket pump: %s", e)
        self._pump = None

        # Close HTTP session with proper resource cleanup
        if self._http and not self._http.closed:
            try:
                await self._http.close()
            except Exception as e:
                wslog.debug("Error closing HTTP session: %s", e)
        self._http = None

        # Clear collaboration state
        self._room_id = None
        self._session_id = None
        self._clear_awareness_state()

        # Clear pending updates
        if self._pending_updates:
            for future in self._pending_updates.values():
                if not future.done():
                    future.cancel()
            self._pending_updates.clear()

    async def is_connected(self) -> bool:
        """True if the WebSocket is open and collaborative session is active."""
        return bool(self._ws and not self._ws.closed)

    async def fetch(self) -> dict[str, Any]:
        """
        Build and return an nbformat-like dict reconstructed from the current YDoc.

        Returns:
            Notebook content as an nbformat-compatible dictionary

        Raises:
            RuntimeError: If the transport is not properly initialized
        """
        await self._ensure_root()
        doc = self._doc
        assert doc is not None
        assert self._ynb is not None, "YNotebook should always be initialized"

        with doc.transaction():
            # exact nbformat-like dict from jupyter_ydoc
            return self._ynb.source

    async def get_cell(self, index: int) -> dict[str, Any]:
        """Return the cell at *index* directly from the Yjs CRDT.

        This is significantly cheaper than ``fetch()`` because it reads a
        single ``YMap`` instead of serialising the entire notebook.
        """
        await self._ensure_root()
        doc = self._doc
        assert doc is not None
        assert self._ynb is not None, "YNotebook should always be initialized"
        with doc.transaction():
            n = self._ynb.cell_number
            if index < 0 or index >= n:
                raise IndexError(f"get_cell: index {index} out of range 0..{n - 1}")
            return self._ynb.get_cell(index)

    async def cell_count(self) -> int:
        """Return the number of cells directly from the Yjs CRDT."""
        await self._ensure_root()
        doc = self._doc
        assert doc is not None
        assert self._ynb is not None, "YNotebook should always be initialized"
        with doc.transaction():
            return self._ynb.cell_number

    async def get_cell_source(self, index: int) -> str:
        """Return the source text of a single cell from the Yjs CRDT.

        Reads only the ``YText`` for the cell's source, avoiding a full
        notebook serialisation.
        """
        await self._ensure_root()
        doc = self._doc
        assert doc is not None
        assert self._ynb is not None, "YNotebook should always be initialized"
        with doc.transaction():
            n = self._ynb.cell_number
            if index < 0 or index >= n:
                raise IndexError(f"get_cell_source: index {index} out of range 0..{n - 1}")
            ycell = self._ynb.ycells[index]
            return ytext_to_str(ycell.get("source"))

    async def append_code_cell(
        self,
        source: str,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> int:
        """Append a code cell; set its YText source for cross-build consistency."""
        await self._ensure_root()
        if tags is not None:
            validate_tags(tags)

        async with self._op_lock:
            doc = self._doc
            assert doc is not None
            assert self._ynb is not None, "YNotebook should always be initialized"
            with doc.transaction():
                # Create the cell with empty source; then populate YText
                ycell = self._ynb.create_ycell(make_code_cell_dict("", metadata, tags=tags))
                self._ynb.ycells.append(ycell)
                idx = self._ynb.cell_number - 1
                try:
                    ysrc = ycell.get("source")
                    if isinstance(ysrc, pycrdt.Text):
                        del ysrc[:]
                        ysrc += source
                    else:
                        ynew = pycrdt.Text(source)
                        ycell["source"] = ynew
                except Exception as e:
                    wslog.warning("append_code_cell: failed to set YText source: %s", e)

            await self._broadcast_update()
            await self._wait_for_sync_completion()
        self._notify({"op": "cells-mutated", "kind": "append_code", "index": idx})
        return idx

    async def insert_code_cell(
        self,
        index: int,
        source: str,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Insert a code cell at a specific index and set its YText source."""
        await self._ensure_root()
        if tags is not None:
            validate_tags(tags)

        async with self._op_lock:
            doc = self._doc
            assert doc is not None
            assert self._ynb is not None, "YNotebook should always be initialized"
            with doc.transaction():
                ycell = self._ynb.create_ycell(make_code_cell_dict("", metadata, tags=tags))
                self._ynb.ycells.insert(index, ycell)
                try:
                    ysrc = ycell.get("source")
                    if isinstance(ysrc, pycrdt.Text):
                        del ysrc[:]
                        ysrc += source
                    else:
                        ynew = pycrdt.Text(source)
                        ycell["source"] = ynew
                except Exception as e:
                    wslog.warning("insert_code_cell: failed to set YText source: %s", e)
            await self._broadcast_update()
            await self._wait_for_sync_completion()
        self._notify({"op": "cells-mutated", "kind": "insert_code", "index": index})

    async def append_markdown_cell(self, source: str, tags: list[str] | None = None) -> int:
        """Append a markdown cell; set its YText source for consistency."""
        await self._ensure_root()
        if tags is not None:
            validate_tags(tags)

        async with self._op_lock:
            doc = self._doc
            assert doc is not None
            assert self._ynb is not None, "YNotebook should always be initialized"
            with doc.transaction():
                ycell = self._ynb.create_ycell(make_md_cell_dict("", tags=tags))
                self._ynb.ycells.append(ycell)
                idx = self._ynb.cell_number - 1
                try:
                    ysrc = ycell.get("source")
                    if isinstance(ysrc, pycrdt.Text):
                        del ysrc[:]
                        ysrc += source
                    else:
                        ynew = pycrdt.Text(source)
                        ycell["source"] = ynew
                except Exception as e:
                    wslog.warning("append_markdown_cell: failed to set YText source: %s", e)
            await self._broadcast_update()
            await self._wait_for_sync_completion()
        self._notify({"op": "cells-mutated", "kind": "append_markdown", "index": idx})
        return idx

    async def insert_markdown_cell(
        self, index: int, source: str, tags: list[str] | None = None
    ) -> None:
        """Insert a markdown cell and set its YText source."""
        await self._ensure_root()
        if tags is not None:
            validate_tags(tags)

        async with self._op_lock:
            doc = self._doc
            assert doc is not None
            assert self._ynb is not None, "YNotebook should always be initialized"
            with doc.transaction():
                ycell = self._ynb.create_ycell(make_md_cell_dict("", tags=tags))
                self._ynb.ycells.insert(index, ycell)
                try:
                    ysrc = ycell.get("source")
                    if isinstance(ysrc, pycrdt.Text):
                        del ysrc[:]
                        ysrc += source
                    else:
                        ynew = pycrdt.Text(source)
                        ycell["source"] = ynew
                except Exception as e:
                    wslog.warning("insert_markdown_cell: failed to set YText source: %s", e)
            await self._broadcast_update()
            await self._wait_for_sync_completion()
        self._notify({"op": "cells-mutated", "kind": "insert_markdown", "index": index})

    async def delete_cell(self, index: int) -> None:
        """Delete cell at index."""
        await self._ensure_root()
        async with self._op_lock:
            doc = self._doc
            assert doc is not None
            assert self._ynb is not None, "YNotebook should always be initialized"
            for _ in range(3):
                with doc.transaction():
                    cells = self._ynb.ycells
                    if isinstance(cells, pycrdt.Array) and 0 <= index < len(cells):
                        del cells[index]
                        break
                await asyncio.sleep(0.05)
            else:
                with doc.transaction():
                    current_len = self._ynb.cell_number
                raise IndexError(f"delete_cell: index {index} out of range 0..{current_len - 1}")
            await self._broadcast_update()
            await self._wait_for_sync_completion()
        self._notify({"op": "cells-mutated", "kind": "delete", "index": index})

    async def _validate_cell_index(self, index: int, operation: str) -> bool:
        await self._ensure_root()
        try:
            doc = self._doc
            assert doc is not None
            assert self._ynb is not None, "YNotebook should always be initialized"
            with doc.transaction():
                current_len = self._ynb.cell_number
                ok = 0 <= index < current_len
                if not ok:
                    wslog.warning(
                        "%s: index %d out of range 0..%d",
                        operation,
                        index,
                        current_len - 1,
                    )
                return ok
        except Exception as e:
            wslog.error("Error validating cell index %d for %s: %s", index, operation, e)
            return False

    async def update_cell_outputs(
        self,
        index: int,
        outputs: list[dict[str, Any]],
        execution_count: int | None,
    ) -> None:
        """Replace outputs (and exec_count) of the code cell at `index`."""
        if outputs is not None and (
            not isinstance(outputs, list) or not all(isinstance(o, dict) for o in outputs)
        ):
            raise TypeError("update_cell_outputs: 'outputs' must be a list of dicts")

        await self._ensure_root()
        doc = self._doc
        assert doc is not None
        assert self._ynb is not None

        with doc.transaction():
            current_len = self._ynb.cell_number
        if not (0 <= index < current_len):
            raise IndexError(
                f"update_cell_outputs: index {index} out of range 0..{current_len - 1}"
            )

        async with self._op_lock:
            doc = self._doc
            assert doc is not None
            assert self._ynb is not None
            try:
                with doc.transaction():
                    cells = self._ynb.ycells
                    ycell = cells[index]
                    cell_dict = ycell.to_py() or {}
                    # Ensure it's a code cell
                    if cell_dict.get("cell_type") != "code":
                        raise TypeError("update_cell_outputs requires a code cell")
                    # Ensure outputs are JSON-serializable
                    try:
                        from json import dumps, loads

                        sanitized = loads(dumps(outputs or []))
                    except Exception:
                        sanitized = [dict(o) for o in (outputs or [])]
                    # Update outputs and execution_count
                    cell_dict["outputs"] = sanitized
                    cell_dict["execution_count"] = execution_count
                    cell_dict.setdefault("metadata", {})
                    # Write back to YMap with proper Y.doc transaction
                    self._ynb.set_cell(index, cell_dict)

                await self._broadcast_update()
                # Reduced wait to minimize race conditions
                if self._pending_updates:
                    await asyncio.wait_for(self._wait_for_sync_completion(), timeout=1.0)

            except TimeoutError:
                wslog.warning("Output update sync timeout for cell %d, continuing", index)
            except Exception as e:
                wslog.error("Failed to update cell %d outputs: %s", index, e)
                raise
        # Notify operation completed successfully
        self._notify({"op": "cells-mutated", "kind": "outputs", "index": index})

    async def update_cell_outputs_delta(
        self,
        index: int,
        outputs: list[dict[str, Any]],
        execution_count: int | None,
        *,
        updated_indices: set[int] | None = None,
        cleared: bool = False,
    ) -> None:
        """
        Incrementally update outputs for a code cell.

        If updated_indices is None, falls back to a full replace.
        """
        if outputs is not None and (
            not isinstance(outputs, list) or not all(isinstance(o, dict) for o in outputs)
        ):
            raise TypeError("update_cell_outputs_delta: 'outputs' must be a list of dicts")

        await self._ensure_root()
        doc = self._doc
        assert doc is not None
        assert self._ynb is not None

        with doc.transaction():
            current_len = self._ynb.cell_number
        if not (0 <= index < current_len):
            raise IndexError(
                f"update_cell_outputs_delta: index {index} out of range 0..{current_len - 1}"
            )

        async with self._op_lock:
            doc = self._doc
            assert doc is not None
            assert self._ynb is not None
            try:
                # Ensure outputs are JSON-serializable
                try:
                    from json import dumps, loads

                    sanitized = loads(dumps(outputs or []))
                except Exception:
                    sanitized = [dict(o) for o in (outputs or [])]

                with doc.transaction():
                    cells = self._ynb.ycells
                    ycell = cells[index]
                    cell_dict = ycell.to_py() or {}
                    if cell_dict.get("cell_type") != "code":
                        raise TypeError("update_cell_outputs_delta requires a code cell")

                    youtputs = ycell.get("outputs") if hasattr(ycell, "get") else ycell["outputs"]
                    if not isinstance(youtputs, pycrdt.Array):
                        youtputs = pycrdt.Array()
                        ycell["outputs"] = youtputs

                    def _clear_outputs() -> None:
                        youtputs.clear()

                    def _set_output(i: int, value: dict[str, Any]) -> None:
                        if i < len(youtputs):
                            try:
                                youtputs[i] = value
                                return
                            except Exception:
                                del youtputs[i]
                                youtputs.insert(i, value)
                                return
                        youtputs.append(value)

                    if cleared:
                        _clear_outputs()
                    elif updated_indices is None:
                        _clear_outputs()
                        for item in sanitized:
                            youtputs.append(item)
                    else:
                        for i in sorted(updated_indices):
                            if 0 <= i < len(sanitized):
                                _set_output(i, sanitized[i])

                    ycell["execution_count"] = execution_count

                await self._broadcast_update()
                if self._pending_updates:
                    await asyncio.wait_for(self._wait_for_sync_completion(), timeout=1.0)
            except TimeoutError:
                wslog.warning("Output delta sync timeout for cell %d, continuing", index)
            except Exception as e:
                wslog.error("Failed to update cell %d outputs delta: %s", index, e)
                raise

        self._notify({"op": "cells-mutated", "kind": "outputs", "index": index})

    async def set_cell_source(self, index: int, source: str) -> None:
        """
        Replace the source text of a cell in the collaborative notebook.

        Args:
            index: Zero-based index of the cell to modify
            source: New source text for the cell

        Raises:
            IndexError: If index is out of range
        """
        await self._ensure_root()
        async with self._op_lock:
            doc = self._doc
            assert doc is not None
            assert self._ynb is not None, "YNotebook should always be initialized"
            for _ in range(3):
                with doc.transaction():
                    cells = self._ynb.ycells
                    if isinstance(cells, pycrdt.Array) and 0 <= index < len(cells):
                        cm = cells[index]
                        ysrc = cm.get("source")
                        if isinstance(ysrc, pycrdt.Text):
                            del ysrc[:]
                            ysrc += source
                        else:
                            ynew = pycrdt.Text(source)
                            cm["source"] = ynew
                        break
                await asyncio.sleep(0.05)
            else:
                with doc.transaction():
                    current_len = self._ynb.cell_number
                raise IndexError(
                    f"set_cell_source: index {index} out of range 0..{current_len - 1}"
                )
            await self._broadcast_update()
            await self._wait_for_sync_completion()
        self._notify({"op": "cells-mutated", "kind": "set_source", "index": index})

    def on_change(self, cb: Callable[[dict[str, Any]], None]) -> None:
        """
        Register a callback to be invoked after save or any cell mutation.

        Args:
            cb: Callback function that receives event dictionaries

        Event types:
            - {"op": "save"}: Manual save operation
            - {"op": "cells-mutated", "kind": "append_code", "index": N}: Code cell appended
            - {"op": "cells-mutated", "kind": "insert_code", "index": N}: Code cell inserted
            - {"op": "cells-mutated", "kind": "append_markdown", "index": N}: Markdown cell appended
            - {"op": "cells-mutated", "kind": "insert_markdown", "index": N}: Markdown cell inserted
            - {"op": "cells-mutated", "kind": "delete", "index": N}: Cell deleted
            - {"op": "cells-mutated", "kind": "set_source", "index": N}: Cell source changed
            - {"op": "cells-mutated", "kind": "outputs", "index": N}: Cell outputs updated
            - {"op": "y-update"}: Yjs document update received from collaborators
        """
        self._on_change.append(cb)

    def _notify(self, evt: dict[str, Any]) -> None:
        """Invoke registered change callbacks; swallow exceptions from handlers."""
        for cb in list(self._on_change):
            try:
                cb(evt)
            except Exception:
                pass

    async def _ensure_root(self) -> None:
        """
        Ensure the top-level 'cells' YArray and notebook-shape fields exist.
        Safe to call repeatedly. Logs the state of the cells array after initialization.
        """
        # Best-effort mini wait to avoid racing when callers hit us immediately after start()
        if not self._initial_sync_done.is_set():
            try:
                await asyncio.wait_for(
                    self._initial_sync_done.wait(),
                    timeout=min(0.5, self._initial_sync_timeout),
                )
            except TimeoutError:
                pass

        doc = self._doc
        assert doc is not None
        if self._cells is not None:
            wslog.debug(
                "_ensure_root: _cells already initialized, len=%d",
                len(self._cells) if isinstance(self._cells, pycrdt.Array) else -1,
            )
            try:
                cell_count = len(self._cells) if isinstance(self._cells, pycrdt.Array) else -1
                for i in range(cell_count):
                    cell = self._cells[i]
                    get = (
                        cell.get
                        if hasattr(cell, "get")
                        else (lambda k, cell=cell: cell.get(k) if isinstance(cell, dict) else None)
                    )
                    cell_type = get("cell_type")
                    cell_id = get("id")
                    cell_source = get("source")
                    src_str = ytext_to_str(cell_source)
                    src_preview = src_str.splitlines()[0] if src_str else ""
                    wslog.debug(
                        "_ensure_root: cell[%d] type=%s id=%s src=%.60s",
                        i,
                        cell_type,
                        cell_id,
                        src_preview,
                    )
            except Exception as e:
                wslog.warning(
                    "_ensure_root: error logging cells array (already initialized): %s",
                    e,
                )
            return

        with doc.transaction():
            cells = self._resolve_cells_top()
            self._root = None
            self._cells = cells  # type: ignore[assignment]
            try:
                cell_count = len(cells) if isinstance(cells, pycrdt.Array) else -1
                wslog.debug("_ensure_root: initialized cells array, len=%d", cell_count)
                if cell_count > 0:
                    for i in range(cell_count):
                        cell = cells[i]
                        get = (
                            cell.get
                            if hasattr(cell, "get")
                            else (
                                lambda k, cell=cell: cell.get(k) if isinstance(cell, dict) else None
                            )
                        )
                        cell_type = get("cell_type")
                        cell_id = get("id")
                        cell_source = get("source")
                        src_str = ytext_to_str(cell_source)
                        src_preview = src_str.splitlines()[0] if src_str else ""
                        wslog.debug(
                            "_ensure_root: cell[%d] type=%s id=%s src=%.60s",
                            i,
                            cell_type,
                            cell_id,
                            src_preview,
                        )
                else:
                    wslog.debug("_ensure_root: cells array is empty after initialization")
            except Exception as e:
                wslog.warning("_ensure_root: error logging cells array: %s", e)

    async def _broadcast_update(self) -> None:
        """
        Send a CRDT UPDATE to the room. Relies on pycrdt to build the FULL
        y-websocket frame (SYNC/UPDATE) so we don't double-wrap.
        """
        if not (self._ws and not self._ws.closed) or not self._doc:
            return

        update_id = self._update_counter
        self._update_counter += 1
        fut = asyncio.Future()
        self._pending_updates[update_id] = fut

        try:
            update = self._doc.get_update()
            if update:
                msg = create_update_message(update)  # FULL frame [0x00 0x02 …]
                await self._ws.send_bytes(msg)
            if not fut.done():
                fut.set_result(None)
        except Exception as e:
            wslog.debug("broadcast update failed: %s", e)
            if not fut.done():
                fut.set_exception(e)
        finally:
            self._pending_updates.pop(update_id, None)

    async def _wait_for_sync_completion(self) -> None:
        """Wait for all pending updates to complete synchronization."""
        if not self._pending_updates:
            return
        current = list(self._pending_updates.values())
        try:
            await asyncio.wait_for(asyncio.gather(*current, return_exceptions=True), timeout=2.0)
        except TimeoutError:
            wslog.debug("Sync completion timeout, cancelling %d pending updates", len(current))
            for fut in current:
                if not fut.done():
                    fut.cancel()
        except Exception as e:
            wslog.debug("Error waiting for sync completion: %s", e)
        finally:
            # Clear completed/cancelled futures
            for k, fut in list(self._pending_updates.items()):
                if fut.done():
                    self._pending_updates.pop(k, None)

    async def _send_awareness(self) -> None:
        """Broadcast current awareness state (pycrdt builds the full frame)."""
        if not (self._ws and not self._ws.closed) or not self._awareness:
            return
        try:
            payload = self._awareness.encode_awareness_update([self._awareness.client_id])
            msg = create_awareness_message(payload)  # FULL frame [0x01 …]
            await self._ws.send_bytes(msg)
            wslog.debug("Sent awareness update")
        except Exception as e:
            wslog.debug("awareness send failed: %s", e)

    async def _request_resync(self, reason: str, backoff: float) -> None:
        """
        Ask the server to resend consistent state by sending a fresh SYNC message.
        Sends the FULL y-websocket frame from pycrdt (no extra wrapping).
        """
        try:
            if backoff > 0:
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    raise
            async with self._op_lock:
                sync_msg = create_sync_message(self._doc)  # type: ignore[arg-type]
            if sync_msg:
                await self._ws.send_bytes(sync_msg)  # already [0x00 …]
                wslog.debug("Requested Yjs resync after %s (backoff=%.2fs)", reason, backoff)
        except Exception as e:
            wslog.debug("Failed to request Yjs resync after %s: %s", reason, e)

    async def _pump_ws(self) -> None:
        """
        Collaboration WebSocket pump.

        - Classifies each binary frame (sync:0/1/2, awareness, auth).
        - For SYNC frames: pass ONLY the sync submessage (data[1:]) to the decoder.
        - Send any decoder reply bytes back AS-IS (pycrdt already framed them correctly).
        - Trips the initial-sync barrier ONLY after a successful sync apply.
        - NEW: once synced, if YNotebook exists and cells len>=1, trip the cells-bootstrap barrier.
        - On reject/apply failure, proactively requests a re-sync with small backoff.
        """
        assert self._ws is not None
        doc = self._doc
        assert doc is not None

        decode_failures = 0
        rejects = 0
        backoff = 0.0
        max_backoff = 1.0

        try:
            while True:
                try:
                    msg = await self._ws.receive()
                except Exception as e:
                    wslog.debug("WS receive error: %s", e)
                    break

                if msg.type == aiohttp.WSMsgType.BINARY:
                    data: bytes = msg.data
                    wslog.debug("WS BINARY len=%d head=%s", len(data), hex_preview(data))

                    ok, kind = looks_like_yws(data)
                    if not ok:
                        rejects += 1
                        lvl = wslog.warning if rejects <= 2 else wslog.debug
                        lvl(
                            "Rejecting invalid yws frame (%s): len=%d head=%s",
                            kind,
                            len(data),
                            hex_preview(data),
                        )
                        backoff = min(max_backoff, backoff + 0.1)
                        await self._request_resync(f"reject:{kind}", backoff)
                        continue

                    if kind == "auth":
                        wslog.debug("WS AUTH frame ignored (non-structural)")
                        continue

                    # inbound awareness is non-structural for the doc;
                    # apply to awareness state and notify callbacks
                    if kind == "awareness":
                        self._apply_awareness_update(data[1:])
                        self._notify_awareness()
                        continue

                    # SYNC:* → pass sync submessage only
                    sub = data[1:]
                    reply, applied_ok = safe_handle_sync_message(sub, doc, logger=wslog)

                    if reply:
                        try:
                            await self._ws.send_bytes(reply)
                        except Exception as e:
                            wslog.debug("Failed to send Yjs sync reply: %s", e)

                    if applied_ok:
                        decode_failures = 0
                        backoff = 0.0
                        if not self._initial_sync_done.is_set():
                            self._initial_sync_done.set()
                            wslog.debug("Initial Yjs state applied; sync barrier set")

                        # detect cells bootstrap (len >= 1) once YNotebook is ready
                        if not self._cells_bootstrapped.is_set() and self._ynb is not None:
                            try:
                                with doc.transaction():
                                    cells = self._ynb.ycells
                                    if isinstance(cells, pycrdt.Array) and len(cells) >= 1:
                                        self._cells_bootstrapped.set()
                                        wslog.debug(
                                            "Cells bootstrap detected (len=%d); ready.",
                                            len(cells),
                                        )
                            except Exception:
                                pass
                    else:
                        decode_failures += 1
                        lvl = wslog.warning if decode_failures == 1 else wslog.debug
                        lvl(
                            "safe_handle_sync_message failed (count=%d)",
                            decode_failures,
                        )
                        backoff = min(max_backoff, backoff + 0.1)
                        await self._request_resync("decode-failure", backoff)

                    try:
                        self._notify({"op": "y-update"})
                    except Exception:
                        pass

                    self._log_cells_snapshot("after y-update")
                    continue

                if msg.type == aiohttp.WSMsgType.TEXT:
                    wslog.debug(
                        "WS TEXT: %s",
                        (
                            (msg.data[:120] + "…")
                            if isinstance(msg.data, str) and len(msg.data) > 120
                            else msg.data
                        ),
                    )
                    continue

                if msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    wslog.debug("Collab WS pump terminating: %s", msg.type)
                    break

                if msg.type in (aiohttp.WSMsgType.PING, aiohttp.WSMsgType.PONG):
                    continue

        except asyncio.CancelledError:
            wslog.debug("WS pump cancelled for %s", self._path)
        except Exception as e:
            wslog.warning("WS pump error for %s: %s", self._path, e)
        finally:
            if not self._initial_sync_done.is_set():
                self._initial_sync_done.set()
                wslog.debug("Pump exiting without initial state; barrier forced open")
            wslog.debug("WS pump stopped for %s", self._path)

    def awareness_client_id(self) -> int | None:
        """Return the local awareness client id, if available."""
        if not self._awareness:
            return None
        return getattr(self._awareness, "client_id", None)

    def get_awareness_states(self) -> dict[int, dict[str, Any]]:
        """Return a snapshot of awareness states indexed by client id."""
        if not self._awareness:
            return {}
        states = getattr(self._awareness, "states", {})
        return dict(states) if isinstance(states, dict) else {}

    def get_awareness_state(self, client_id: int) -> dict[str, Any] | None:
        """Return the awareness state for a given client id."""
        return self.get_awareness_states().get(client_id)

    async def set_awareness_state(self, state: dict[str, Any]) -> None:
        """Replace the local awareness state and broadcast it."""
        if not self._awareness:
            return
        self._awareness.set_local_state(state)
        await self._send_awareness()
        self._notify_awareness()

    async def set_awareness_field(self, key: str, value: Any) -> None:
        """Set a single field in the local awareness state and broadcast it."""
        if not self._awareness:
            return
        setter = getattr(self._awareness, "set_local_state_field", None)
        if callable(setter):
            setter(key, value)
        else:
            getter = getattr(self._awareness, "get_local_state", None)
            state = dict(getter() or {}) if callable(getter) else {}
            state[key] = value
            self._awareness.set_local_state(state)
        await self._send_awareness()
        self._notify_awareness()

    def on_awareness_change(self, cb: Callable[[dict[int, dict[str, Any]]], None]) -> None:
        """Register a callback invoked when awareness state changes."""
        self._on_awareness_change.append(cb)

    def _notify_awareness(self) -> None:
        """Invoke awareness callbacks; swallow exceptions from handlers."""
        states = self.get_awareness_states()
        for cb in list(self._on_awareness_change):
            try:
                cb(states)
            except Exception as e:
                wslog.debug("Awareness callback failed: %s", e)

    def _setup_awareness_callbacks(self) -> None:
        """Attach awareness observers when supported by the backend."""
        if not self._awareness:
            return
        observe = getattr(self._awareness, "observe", None)
        if not callable(observe):
            return

        def _on_change(topic: str, event: tuple[dict, Any]) -> None:
            # Expected shape: (topic="update", (changes_dict, origin))
            try:
                if topic == "update":
                    _, origin = event
                    if origin == "local":
                        asyncio.create_task(self._send_awareness())
                self._notify_awareness()
            except Exception as e:
                wslog.debug("Awareness observer error: %s", e)

        self._awareness_observer = observe(_on_change)

    def _start_awareness_ping(self) -> None:
        """Start background awareness ping if supported by the backend."""
        if not self._awareness or self._awareness_ping:
            return
        starter = getattr(self._awareness, "_start", None)
        if callable(starter):
            self._awareness_ping = asyncio.create_task(starter())

    def _clear_awareness_state(self) -> None:
        """Stop awareness observers and background ping."""
        if self._awareness and self._awareness_observer:
            unobserve = getattr(self._awareness, "unobserve", None)
            if callable(unobserve):
                try:
                    unobserve(self._awareness_observer)
                except Exception as e:
                    wslog.debug("Failed to unobserve awareness: %s", e)
        self._awareness_observer = None
        if self._awareness_ping and not self._awareness_ping.done():
            self._awareness_ping.cancel()
        self._awareness_ping = None

    def _apply_awareness_update(self, payload: bytes) -> None:
        """Apply an incoming awareness update payload to local state."""
        if not self._awareness:
            return
        try:
            self._awareness.apply_awareness_update(payload, origin="remote")
        except Exception as e:
            wslog.debug("Failed to apply awareness update: %s", e)

    def _resolve_cells_top(self) -> Any:
        """
        Resolve the cells YArray from the YNotebook schema.

        Returns:
            The ycells YArray from the YNotebook instance.

        Raises:
            RuntimeError: If YNotebook is not initialized.
        """
        if self._ynb is None:
            raise RuntimeError("YNotebook not initialized")

        ynb = self._ynb
        ymeta = getattr(ynb, "ymeta", getattr(ynb, "_ymeta", None))
        if ymeta is not None:
            if "metadata" not in ymeta or ymeta.get("metadata") is None:
                ymeta["metadata"] = pycrdt.Map()
            if ymeta.get("nbformat") is None:
                ymeta["nbformat"] = 4
            if ymeta.get("nbformat_minor") is None:
                ymeta["nbformat_minor"] = 5
        return ynb.ycells

    def _log_cells_snapshot(self, where: str) -> None:
        """Debug helper: log first few cells."""
        try:
            if self._cells is None or not isinstance(self._cells, pycrdt.Array):
                wslog.debug("%s: cells snapshot: <not ready>", where)
                return
            doc = self._doc
            if not doc:
                wslog.debug("%s: cells snapshot: <no doc>", where)
                return
            with doc.transaction():
                n = len(self._cells)
                wslog.debug("%s: cells snapshot len=%d", where, n)
                for i in range(min(n, 4)):
                    try:
                        c = self._cells[i]
                    except Exception:
                        wslog.debug("%s: snapshot aborted (array changed at i=%d)", where, i)
                        break
                    get = (
                        c.get
                        if hasattr(c, "get")
                        else (lambda k, c=c: c.get(k) if isinstance(c, dict) else None)
                    )
                    ct = get("cell_type")
                    cid = get("id")
                    src = get("source")
                    s = ytext_to_str(src)
                    wslog.debug(
                        "%s: cell[%d] type=%s id=%s src=%.60s",
                        where,
                        i,
                        ct,
                        cid,
                        (s or "").splitlines()[0] if s else "",
                    )
        except Exception as e:
            wslog.debug("%s: snapshot logging failed: %s", where, e)
