"""
Remote kernel transport for agent-jupyter-toolkit.

This module provides ServerTransport, which connects to remote Jupyter kernels
via HTTP REST API and WebSocket channels. It enables AI agents to execute code
on remote Jupyter servers with real-time output streaming and robust connection
management.

Key features:
- HTTP + WebSocket communication with remote Jupyter servers
- Background WebSocket pump for connection stability
- Request correlation and concurrent execution support
- Automatic reconnection and kernel management
- Real-time output streaming via callbacks

Architecture:
- HTTP API for kernel lifecycle (create, delete, status)
- WebSocket channels for real-time message exchange
- Background pump prevents connection timeouts
- Request/response correlation via message IDs
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import aiohttp

from ..hooks import kernel_hooks
from ..messages import (
    build_complete_request,
    build_execute_request,
    build_history_request,
    build_inspect_request,
    build_is_complete_request,
    build_kernel_info_request,
    fold_iopub_events,
)
from ..transport import KernelTransport
from ..types import (
    CompleteResult,
    ExecutionResult,
    HistoryEntry,
    HistoryResult,
    InspectResult,
    IsCompleteResult,
    KernelInfoResult,
    OutputCallback,
    ServerConfig,
)

logger = logging.getLogger(__name__)
ws_logger = logging.getLogger(__name__ + ".ws")


class ServerTransport(KernelTransport):
    """
    Remote kernel transport using HTTP REST API and WebSocket channels.

    This transport connects to remote Jupyter servers and provides execution
    capabilities for AI agents. It manages kernel lifecycle, handles network
    connectivity issues, and streams real-time outputs.

    Connection Management:
        - HTTP session for REST API calls (kernel management)
        - WebSocket connection for real-time message exchange
        - Background pump task to maintain WebSocket connectivity
        - Automatic reconnection on connection failures

    Concurrency:
        - Per-transport execution lock prevents message interleaving
        - Request correlation via parent message IDs
        - Asynchronous output streaming via callbacks

    Features:
        - Idempotent start() for robust connection handling
        - Background WebSocket pump prevents idle timeouts
        - Structured logging with configurable verbosity
        - Support for notebook sessions and standalone kernels
    """

    def __init__(self, cfg: ServerConfig) -> None:
        """
        Initialize remote transport with server configuration.

        Args:
            cfg: Server configuration containing URL, authentication,
                and optional notebook path for session-based kernels.
        """
        self.cfg = cfg
        self._base = cfg.base_url.rstrip("/")
        self._client_session_id = uuid.uuid4().hex  # For debugging/tracing

        # HTTP session for REST API calls
        self._session: aiohttp.ClientSession | None = None

        # Kernel and WebSocket state
        self._kernel_id: str | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None

        # Background pump maintains WebSocket connectivity
        self._pump_task: asyncio.Task | None = None
        self._inbox: asyncio.Queue = asyncio.Queue()

        # Execution lock prevents concurrent request interference
        self._exec_lock: asyncio.Lock = asyncio.Lock()

    @property
    def kernel_id(self) -> str | None:
        """
        Get the current remote kernel ID.

        Returns:
            Optional[str]: Kernel ID if connected, None otherwise.

        Useful for debugging, logging, and manual kernel management.
        """
        return self._kernel_id

    async def start(self) -> None:
        """
        Connect to remote kernel and establish WebSocket channels.

        This method implements smart connection logic:
        1. If WebSocket is already connected, return immediately
        2. If kernel exists but WebSocket is closed, reconnect WebSocket only
        3. Otherwise, create/attach to kernel and establish new connection
        4. Start background pump to maintain connection stability

        Connection Types:
            - Session-based: Attaches to existing notebook session kernel
            - Standalone: Creates new kernel for code execution only

        The method is idempotent and safe to call multiple times.

        Raises:
            aiohttp.ClientError: If server is unreachable or authentication fails
            RuntimeError: If kernel creation or WebSocket connection fails
        """
        # Early return if already connected
        if self._ws is not None and not self._ws.closed:
            logger.debug("start(): already connected (kernel_id=%s)", self._kernel_id)
            return

        # Initialize HTTP session with authentication
        if self._session is None:
            self._session = aiohttp.ClientSession(headers=self._auth_headers())

        # If we already have a kernel id, re-open WS only (no new kernel)
        if self._kernel_id:
            ws_url = self._build_ws_url()
            logger.debug("Reconnecting WS → %s", self._sanitize(ws_url))
            self._ws = await self._session.ws_connect(ws_url, heartbeat=30)
            # Reset inbox so no stale frames linger across reconnects
            self._inbox = asyncio.Queue()
            self._start_pump()
            logger.info(
                "WS reconnected (kernel_id=%s, session_id=%s)",
                self._kernel_id,
                self._client_session_id,
            )
            return

        logger.info(
            "Connecting to Jupyter base=%s (notebook_path=%s)",
            self._base,
            self.cfg.notebook_path or "<standalone kernel>",
        )

        # Create or attach to a kernel
        if self.cfg.notebook_path:
            # Notebook mode: find existing session or create new one for the file
            sess = await self._get_session_for_path(self.cfg.notebook_path)
            if not sess:
                logger.info(
                    "No session for %r → creating (kernel=%s)",
                    self.cfg.notebook_path,
                    self.cfg.kernel_name,
                )
                sess = await self._create_session_for_path(
                    self.cfg.notebook_path, self.cfg.kernel_name
                )
            self._kernel_id = sess["kernel"]["id"]
        else:
            # Standalone mode: create isolated kernel not tied to any notebook
            self._kernel_id = await self._create_kernel(self.cfg.kernel_name)

        logger.info("Kernel ready: kernel_id=%s", self._kernel_id)

        # Connect WS (send heartbeat pings every 30s to avoid idle timeouts)
        ws_url = self._build_ws_url()
        logger.debug("WS connect → %s", self._sanitize(ws_url))
        self._ws = await self._session.ws_connect(ws_url, heartbeat=30)
        # Fresh inbox queue to prevent stale messages
        self._inbox = asyncio.Queue()
        self._start_pump()
        logger.info("WS connected (session_id=%s).", self._client_session_id)

    async def shutdown(self) -> None:
        """
        Clean shutdown of all resources and connections.

        Performs graceful cleanup in this order:
        1. Cancel and await background pump task
        2. Close WebSocket connection
        3. Delete remote kernel (best effort)
        4. Close HTTP session
        5. Clear all references

        The shutdown is fault-tolerant - each step attempts cleanup
        even if previous steps failed. This prevents resource leaks
        and ensures clean disconnection from the remote server.

        Note:
            After shutdown, this transport cannot be reused.
            Create a new ServerTransport instance for further operations.
        """
        # Stop background pump task first
        if self._pump_task:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except asyncio.CancelledError:
                pass
            self._pump_task = None

        try:
            # Close WebSocket connection
            if self._ws:
                await self._ws.close()
                logger.info("WS closed.")

            # Attempt to delete remote kernel (best effort)
            if self._kernel_id and self._session:
                try:
                    async with self._session.delete(f"{self._base}/api/kernels/{self._kernel_id}"):
                        logger.info("Kernel deleted: %s", self._kernel_id)
                except Exception as e:
                    logger.warning("Kernel delete failed: %s", e)
        finally:
            # Always close HTTP session and clear state
            if self._session:
                await self._session.close()
                logger.debug("HTTP session closed.")
        self._ws = None
        self._session = None
        self._kernel_id = None

    async def is_alive(self) -> bool:
        """
        Check if the remote kernel is alive and responsive.

        This method makes an HTTP GET request to the kernel's status endpoint
        to verify it exists and is responding. It does not check WebSocket
        connectivity - use start() to ensure full connection.

        Returns:
            bool: True if kernel responds to HTTP status check,
                  False if kernel is dead, unreachable, or not created.

        Note:
            This is a lightweight health check. A True result indicates
            the kernel process is running but doesn't guarantee WebSocket
            connectivity for code execution.
        """
        if not (self._session and self._kernel_id):
            return False

        try:
            async with self._session.get(f"{self._base}/api/kernels/{self._kernel_id}") as r:
                ok = r.status == 200
                logger.debug("is_alive(kernel_id=%s) → %s", self._kernel_id, ok)
                return ok
        except Exception as e:
            logger.debug("is_alive(): error %s", e)
            return False

    async def execute(
        self,
        code: str,
        *,
        timeout: float | None = None,
        output_callback: OutputCallback | None = None,
        store_history: bool = True,
        allow_stdin: bool = False,
        stop_on_error: bool = True,
    ) -> ExecutionResult:
        """
        Execute code on the remote kernel with real-time output streaming.

        This method sends code to the remote kernel via WebSocket and correlates
        responses using parent message IDs. It provides comprehensive execution
        with concurrent safety and automatic reconnection.

        Features:
            - Serialized execution prevents message interleaving
            - Automatic WebSocket reconnection if connection dropped
            - Real-time output streaming via callbacks
            - Request correlation for concurrent safety
            - Comprehensive error handling and logging

        Args:
            code: Python code to execute remotely. Can be multi-line.
            timeout: Maximum execution time in seconds. None = no client timeout.
            output_callback: Optional async callback for real-time outputs.
                           Signature: callback(outputs: List[Dict], execution_count: Optional[int])
            store_history: If True, code is stored in kernel's input history.
            allow_stdin: If True, kernel can request user input via stdin.
                        Should be False for headless AI agent usage.
            stop_on_error: If True, kernel stops processing on first error.

        Returns:
            ExecutionResult: Complete execution information including:
                - status: "ok", "error", or "abort"
                - execution_count: Kernel's execution counter
                - outputs: List of display outputs (text, images, etc.)
                - error_message: Description if execution failed

        Raises:
            RuntimeError: If transport hasn't been started via start()
            asyncio.TimeoutError: If execution exceeds timeout
            aiohttp.ClientError: If network connectivity fails

        Note:
            This method is thread-safe and uses an internal lock to prevent
            concurrent execution requests from interfering with each other.
        """
        async with self._exec_lock:
            # Validate transport state
            if not (self._session and self._kernel_id):
                raise RuntimeError("ServerTransport not started. Call start() first.")

            # Auto-reconnect WebSocket if connection was lost
            if self._ws is None or self._ws.closed:
                logger.debug("execute(): WSocket closed → reopening")
                await self.start()

            # Clear any stale messages from previous requests
            try:
                while True:
                    self._inbox.get_nowait()
            except asyncio.QueueEmpty:
                pass

            req = build_execute_request(
                code,
                silent=False,
                store_history=store_history,
                allow_stdin=allow_stdin,
                stop_on_error=stop_on_error,
            )
            req["channel"] = "shell"
            req.setdefault("buffers", [])
            req["header"]["session"] = self._client_session_id
            req["msg_type"] = req["header"].get("msg_type", "execute_request")
            request_id = req["header"]["msg_id"]

            preview = (code or "").splitlines()[0][:120]
            logger.info(
                "execute(request_id=%s, timeout=%s) code=%r ...", request_id, timeout, preview
            )

            # Trigger pre-execution hooks for instrumentation/logging
            kernel_hooks.trigger_before_execute_hooks(code)

            await self._send_shell(req)

            try:
                # Consume frames for this request; keep a running fold so
                # the output_callback always receives nbformat-shaped outputs.
                events: list[dict] = []
                outputs: list[dict] = []
                execution_count: int | None = None

                async for msg in self._collect_for_request_stream(request_id, timeout=timeout):
                    events.append(msg)

                    # Trigger hooks for each message (consistent with LocalTransport)
                    kernel_hooks.trigger_output_hooks(msg)

                    msg_type = (msg.get("header") or {}).get("msg_type")
                    content = msg.get("content") or {}

                    if msg_type == "execute_input":
                        # Kernel started executing; reveal execution_count ASAP
                        execution_count = content.get("execution_count")
                        if output_callback:
                            await output_callback(outputs, execution_count)

                    elif msg_type in (
                        "stream",
                        "display_data",
                        "update_display_data",
                        "execute_result",
                        "error",
                    ):
                        folded = fold_iopub_events(events)
                        outputs = folded.outputs or []
                        if output_callback:
                            await output_callback(outputs, execution_count)

                    elif msg_type == "clear_output":
                        outputs = []
                        if output_callback:
                            await output_callback(outputs, execution_count)

                # Final fold → return complete ExecutionResult
                res = fold_iopub_events(events)

                # Trigger post-execution hooks for instrumentation/cleanup
                kernel_hooks.trigger_after_execute_hooks(res)

                logger.info(
                    "execute(%s) → status=%s, exec_count=%s, outputs=%d, stdout_len=%d",
                    request_id,
                    res.status,
                    res.execution_count,
                    len(res.outputs or []),
                    len(res.stdout or ""),
                )
                return res

            except Exception as e:
                # Trigger error hooks for consistent error handling
                kernel_hooks.trigger_on_error_hooks(e)
                logger.exception("execute(%s) failed: %s", request_id, e)
                raise

    # ── Introspection / control ──────────────────────────────────────────

    async def interrupt(self) -> None:
        """Interrupt the running kernel via the REST API."""
        if not (self._session and self._kernel_id):
            raise RuntimeError("ServerTransport not started. Call start() first.")
        async with self._session.post(f"{self._base}/api/kernels/{self._kernel_id}/interrupt") as r:
            r.raise_for_status()
        logger.info("Kernel interrupted (kernel_id=%s)", self._kernel_id)

    async def complete(self, code: str, cursor_pos: int) -> CompleteResult:
        """Request tab-completion from the remote kernel."""
        reply = await self._shell_request(build_complete_request(code, cursor_pos))
        content = reply.get("content", {})
        return CompleteResult(
            matches=content.get("matches", []),
            cursor_start=content.get("cursor_start", 0),
            cursor_end=content.get("cursor_end", 0),
            status=content.get("status", "ok"),
            metadata=content.get("metadata", {}),
        )

    async def inspect(
        self,
        code: str,
        cursor_pos: int,
        detail_level: int = 0,
    ) -> InspectResult:
        """Inspect an object at the cursor position on the remote kernel."""
        reply = await self._shell_request(build_inspect_request(code, cursor_pos, detail_level))
        content = reply.get("content", {})
        return InspectResult(
            found=content.get("found", False),
            data=content.get("data", {}),
            metadata=content.get("metadata", {}),
            status=content.get("status", "ok"),
        )

    async def is_complete(self, code: str) -> IsCompleteResult:
        """Check code completeness on the remote kernel."""
        reply = await self._shell_request(build_is_complete_request(code))
        content = reply.get("content", {})
        return IsCompleteResult(
            status=content.get("status", "unknown"),
            indent=content.get("indent", ""),
        )

    async def history(
        self,
        *,
        output: bool = False,
        raw: bool = True,
        hist_access_type: str = "tail",
        n: int = 10,
    ) -> HistoryResult:
        """Retrieve execution history from the remote kernel."""
        reply = await self._shell_request(
            build_history_request(
                output=output,
                raw=raw,
                hist_access_type=hist_access_type,
                n=n,
            )
        )
        content = reply.get("content", {})
        entries = [
            HistoryEntry(
                session=entry[0],
                line_number=entry[1],
                input=entry[2] if len(entry) > 2 else "",
                output=entry[3] if len(entry) > 3 else None,
            )
            for entry in content.get("history", [])
        ]
        return HistoryResult(history=entries, status=content.get("status", "ok"))

    async def kernel_info(self) -> KernelInfoResult:
        """Retrieve metadata about the remote kernel."""
        reply = await self._shell_request(build_kernel_info_request())
        content = reply.get("content", {})
        lang = content.get("language_info", {})
        return KernelInfoResult(
            protocol_version=content.get("protocol_version", ""),
            implementation=content.get("implementation", ""),
            implementation_version=content.get("implementation_version", ""),
            language_info=lang,
            banner=content.get("banner", ""),
            status=content.get("status", "ok"),
        )

    async def _shell_request(self, req: dict) -> dict:
        """
        Send a shell-channel request and wait for the matching reply.

        This is the generic workhorse for all non-execute shell requests.
        It handles connection validation, inbox draining, request correlation,
        and timeout handling.
        """
        async with self._exec_lock:
            if not (self._session and self._kernel_id):
                raise RuntimeError("ServerTransport not started. Call start() first.")
            if self._ws is None or self._ws.closed:
                await self.start()

            # Drain stale messages
            try:
                while True:
                    self._inbox.get_nowait()
            except asyncio.QueueEmpty:
                pass

            req["channel"] = "shell"
            req.setdefault("buffers", [])
            req["header"]["session"] = self._client_session_id
            req["msg_type"] = req["header"].get("msg_type", "unknown")
            request_id = req["header"]["msg_id"]

            await self._send_shell(req)

            # Wait for the reply (match on parent_header.msg_id)
            deadline = asyncio.get_running_loop().time() + 30
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError(f"Timeout waiting for reply to {req['msg_type']}")
                try:
                    raw = await asyncio.wait_for(self._inbox.get(), min(remaining, 0.5))
                except TimeoutError:
                    continue
                msg = raw.get("msg") or raw
                header = msg.get("header") or {}
                parent = msg.get("parent_header") or {}
                msg_type = header.get("msg_type", "")
                parent_id = parent.get("msg_id")

                # Match the reply for our request
                if parent_id == request_id and msg_type.endswith("_reply"):
                    return msg

    async def _collect_for_request_stream(self, request_id: str, *, timeout: float | None):
        """
        Yield each kernel event for this request as it arrives (streaming).
        We only consider DONE after seeing BOTH:
        - iopub status: idle        with parent_header.msg_id == request_id
        - shell execute_reply       with parent_header.msg_id == request_id

        Frames without a parent_id are yielded (so they can be folded into outputs),
        but they DO NOT advance the done-condition.
        """
        queue = self._inbox
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else (loop.time() + timeout)

        seen_idle_for_req = False
        seen_reply_for_req = False

        while True:
            if deadline is not None and loop.time() > deadline:
                raise TimeoutError("Timeout waiting for kernel events")

            try:
                raw = await asyncio.wait_for(queue.get(), 0.25)
            except TimeoutError:
                continue

            # tolerate {"msg": {...}} envelopes
            msg = raw.get("msg") or raw

            header = msg.get("header") or {}
            parent_hdr = msg.get("parent_header") or {}
            content = msg.get("content") or {}

            msg_type = header.get("msg_type")
            parent_id = parent_hdr.get("msg_id")

            # Skip unrelated frames when a parent_id is present
            if parent_id and parent_id != request_id:
                continue

            # Advance done-condition ONLY when parent_id matches this request
            if (
                msg_type == "status"
                and content.get("execution_state") == "idle"
                and parent_id == request_id
            ):
                seen_idle_for_req = True
            elif msg_type == "execute_reply" and parent_id == request_id:
                seen_reply_for_req = True

            if ws_logger.isEnabledFor(logging.DEBUG):
                ws_logger.debug(
                    "stream frame: type=%r parent_id=%r seen_idle=%s seen_reply=%s",
                    msg_type,
                    parent_id,
                    seen_idle_for_req,
                    seen_reply_for_req,
                )

            yield msg

            if seen_idle_for_req and seen_reply_for_req:
                break

    async def _collect_for_request(self, request_id: str, *, timeout: float | None) -> list[dict]:
        """
        Batch collector (non-streaming) retained for completeness and debugging.
        Returns a list of events after both idle and execute_reply (or timeout).
        """
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else (loop.time() + timeout)

        events: list[dict] = []
        got_idle = False
        got_reply = False
        n = 0

        while True:
            timeout_remaining = None if deadline is None else max(0.0, deadline - loop.time())
            try:
                frame = await asyncio.wait_for(self._inbox.get(), timeout_remaining)
            except TimeoutError:
                logger.warning("collect_for_request(%s) timeout after %ss", request_id, timeout)
                events.append(
                    {
                        "msg_type": "error",
                        "content": {"ename": "TimeoutError", "evalue": "", "traceback": []},
                    }
                )
                break

            if frame.get("msg_type") == "__ws_closed__":
                logger.warning("collect_for_request(%s): WS closed", request_id)
                break

            inner = frame.get("msg") or frame
            channel = inner.get("channel") or frame.get("channel")
            header = inner.get("header") or frame.get("header") or {}
            parent = inner.get("parent_header") or frame.get("parent_header") or {}
            msg_type = inner.get("msg_type") or header.get("msg_type")
            content = inner.get("content") or frame.get("content") or {}
            parent_id = parent.get("msg_id")

            if ws_logger.isEnabledFor(logging.DEBUG):
                ws_logger.debug("chan=%r type=%r parent_id=%r", channel, msg_type, parent_id)

            if parent_id is not None and parent_id != request_id:
                continue

            if msg_type in (
                "stream",
                "display_data",
                "update_display_data",
                "execute_result",
                "error",
                "execute_input",
                "execute_reply",
                "status",
            ):
                events.append({"msg_type": msg_type, "content": content})
                n += 1

                if (
                    channel == "iopub"
                    and msg_type == "status"
                    and content.get("execution_state") == "idle"
                ):
                    got_idle = True
                if channel == "shell" and msg_type == "execute_reply":
                    got_reply = True

                if got_idle and got_reply:
                    logger.debug(
                        "collect_for_request(%s) done: events=%d (idle & reply seen)", request_id, n
                    )
                    break

        return events

    def _auth_headers(self) -> dict[str, str]:
        """
        Build HTTP headers with authentication and user preferences.

        Returns:
            Dict containing Accept header, optional Authorization token,
            and any user-supplied headers from configuration.
        """
        h: dict[str, str] = {"Accept": "application/json"}
        if self.cfg.token:
            h["Authorization"] = f"Token {self.cfg.token}"
        if self.cfg.headers:
            h.update(self.cfg.headers)
        logger.debug(
            "Auth headers prepared (token=%s, extra=%s)",
            "yes" if self.cfg.token else "no",
            bool(self.cfg.headers),
        )
        return h

    def _build_ws_url(self) -> str:
        """
        Construct WebSocket URL for kernel channels with authentication.

        Converts HTTP(S) base URL to WS(S) and adds session ID and token
        parameters for proper routing and authentication.

        Returns:
            WebSocket URL for /api/kernels/{kernel_id}/channels endpoint.
        """
        if self._base.startswith("https://"):
            ws_base = "wss://" + self._base[len("https://") :]
        elif self._base.startswith("http://"):
            ws_base = "ws://" + self._base[len("http://") :]
        else:
            ws_base = self._base
        qs = f"session_id={self._client_session_id}"
        if self.cfg.token:
            qs += f"&token={self.cfg.token}"
        return f"{ws_base}/api/kernels/{self._kernel_id}/channels?{qs}"

    def _sanitize(self, url: str) -> str:
        """Remove authentication tokens from URLs for safe logging."""
        return url.replace(self.cfg.token, "****") if self.cfg.token else url

    async def _get_session_for_path(self, path: str) -> dict | None:
        """Find existing notebook session for the given path."""
        assert self._session is not None
        async with self._session.get(f"{self._base}/api/sessions") as r:
            r.raise_for_status()
            sessions = await r.json()
        logger.debug("Sessions listed for path=%r → %d", path, len(sessions or []))
        for s in sessions:
            if s.get("path") == path and s.get("kernel"):
                return s
        return None

    async def _create_session_for_path(self, path: str, kernel_name: str) -> dict:
        """Create new notebook session with specified kernel type."""
        assert self._session is not None
        async with self._session.post(
            f"{self._base}/api/sessions",
            json={"path": path, "type": "notebook", "kernel": {"name": kernel_name}},
        ) as r:
            r.raise_for_status()
            sess = await r.json()
        logger.info(
            "Session created for path=%r kernel=%s (kernel_id=%s)",
            path,
            kernel_name,
            sess["kernel"]["id"],
        )
        return sess

    async def _create_kernel(self, kernel_name: str) -> str:
        """Create standalone kernel and return its ID."""
        assert self._session is not None
        async with self._session.post(
            f"{self._base}/api/kernels",
            json={"name": kernel_name},
        ) as r:
            r.raise_for_status()
            data = await r.json()
        logger.info("Kernel created: kernel_id=%s (name=%s)", data["id"], kernel_name)
        return data["id"]

    async def _send_shell(self, msg: dict) -> None:
        """Send message to kernel's shell channel via WebSocket."""
        assert self._ws is not None
        try:
            await self._ws.send_json(msg)
        except Exception as e:
            logger.exception("WS send failed: %s", e)
            raise

    def _start_pump(self) -> None:
        """Start background WebSocket pump task for continuous message processing."""
        if self._pump_task is None or self._pump_task.done():
            self._pump_task = asyncio.create_task(self._pump_ws(), name="jat-ws-pump")

    async def _pump_ws(self) -> None:
        """
        Background task that continuously reads WebSocket frames.

        This pump maintains connection stability by:
        - Processing heartbeat/ping messages
        - Forwarding kernel messages to the inbox queue
        - Handling connection errors gracefully
        - Preventing idle connection timeouts

        The pump runs until cancelled or WebSocket closes.
        """
        assert self._ws is not None
        try:
            while True:
                msg = await self._ws.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        # Kernel channels send canonical Jupyter messages (JSON)
                        self._inbox.put_nowait(msg.json())
                    except Exception:
                        # If parsing fails, skip this frame
                        continue
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    # Not used by kernel channels
                    continue
                elif msg.type in (aiohttp.WSMsgType.PING, aiohttp.WSMsgType.PONG):
                    # Handled by aiohttp; nothing to enqueue
                    continue
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
        finally:
            # Notify collectors that WS closed so they can bail quickly
            try:
                self._inbox.put_nowait({"msg_type": "__ws_closed__", "content": {}})
            except Exception:
                pass
