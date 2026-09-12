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

from ..callbacks import OutputCallbackDispatcher
from ..execution_state import ExecutionState
from ..history import decode_history_entries
from ..hooks import kernel_hooks
from ..messages import (
    build_complete_request,
    build_control_request,
    build_execute_request,
    build_history_request,
    build_inspect_request,
    build_is_complete_request,
    build_kernel_info_request,
)
from ..transport import KernelTransport
from ..types import (
    CompleteResult,
    ExecutionResult,
    HistoryResult,
    InspectResult,
    IsCompleteResult,
    KernelDisconnectedError,
    KernelInfoResult,
    OutputCallback,
    ServerConfig,
    UnsupportedKernelCapabilityError,
)
from ..websocket_codec import deserialize_binary_message, serialize_binary_message

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
        self._server_session_id: str | None = None
        self._owns_kernel = False
        self._owns_session = False
        self._ws: aiohttp.ClientWebSocketResponse | None = None

        # Background pump maintains WebSocket connectivity
        self._pump_task: asyncio.Task | None = None
        self._inbox: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._request_queues: dict[str, asyncio.Queue] = {}

        # Execution lock prevents concurrent request interference
        self._exec_lock: asyncio.Lock = asyncio.Lock()
        self._control_lock: asyncio.Lock = asyncio.Lock()
        self._supported_features: set[str] | None = None

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
        """Start or reconnect and clean up resources created by a failed start."""
        had_kernel = self._kernel_id is not None
        try:
            await self._start()
        except BaseException:
            if not had_kernel and (self._owns_kernel or self._owns_session):
                await self._shutdown(force_kernel=False)
            else:
                await self._close_websocket()
                if self._session is not None:
                    await self._session.close()
                    self._session = None
            raise

    async def _start(self) -> None:
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
            self._session = aiohttp.ClientSession(
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(total=self.cfg.request_timeout),
            )

        # If we already have a kernel id, re-open WS only (no new kernel)
        if self._kernel_id:
            ws_url = self._build_ws_url()
            logger.debug("Reconnecting WS → %s", self._sanitize(ws_url))
            self._ws = await self._session.ws_connect(ws_url, heartbeat=30)
            # Reset inbox so no stale frames linger across reconnects
            self._inbox = asyncio.Queue(maxsize=32)
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
                self._owns_session = True
                self._owns_kernel = True
            self._server_session_id = sess.get("id")
            self._kernel_id = sess["kernel"]["id"]
        else:
            # Standalone mode: create isolated kernel not tied to any notebook
            self._kernel_id = await self._create_kernel(self.cfg.kernel_name)
            self._owns_kernel = True

        logger.info("Kernel ready: kernel_id=%s", self._kernel_id)

        # Connect WS (send heartbeat pings every 30s to avoid idle timeouts)
        ws_url = self._build_ws_url()
        logger.debug("WS connect → %s", self._sanitize(ws_url))
        self._ws = await self._session.ws_connect(ws_url, heartbeat=30)
        # Fresh inbox queue to prevent stale messages
        self._inbox = asyncio.Queue(maxsize=32)
        self._start_pump()
        logger.info("WS connected (session_id=%s).", self._client_session_id)

    async def shutdown(self) -> None:
        """Close this client and delete only sessions or kernels it created."""
        await self._shutdown(force_kernel=False)

    async def shutdown_kernel(self) -> None:
        """Explicitly terminate the remote kernel, including a borrowed kernel."""
        await self._shutdown(force_kernel=True)

    async def _shutdown(self, *, force_kernel: bool) -> None:
        await self._close_websocket()
        try:
            session_deleted = False
            if self._session and self._owns_session and self._server_session_id:
                try:
                    async with self._session.delete(
                        f"{self._base}/api/sessions/{self._server_session_id}"
                    ) as response:
                        response.raise_for_status()
                    session_deleted = True
                    logger.info("Session deleted: %s", self._server_session_id)
                except Exception as e:
                    logger.warning("Session delete failed: %s", e)
            if (
                self._session
                and self._kernel_id
                and (self._owns_kernel or force_kernel)
                and not session_deleted
            ):
                try:
                    async with self._session.delete(
                        f"{self._base}/api/kernels/{self._kernel_id}"
                    ) as response:
                        response.raise_for_status()
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
        self._server_session_id = None
        self._owns_kernel = False
        self._owns_session = False
        self._supported_features = None
        self._request_queues.clear()

    @property
    def owns_kernel(self) -> bool:
        return self._owns_kernel

    @property
    def owns_session(self) -> bool:
        return self._owns_session

    @property
    def server_session_id(self) -> str | None:
        return self._server_session_id

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
        silent: bool = False,
        store_history: bool = True,
        user_expressions: dict | None = None,
        metadata: dict | None = None,
        subshell_id: str | None = None,
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
                - outcome/timed_out: Whether completion was observed

        Raises:
            RuntimeError: If transport hasn't been started via start()
            KernelDisconnectedError: If the WebSocket closes before completion
            aiohttp.ClientError: If network connectivity fails

        Note:
            This method is thread-safe and uses an internal lock to prevent
            concurrent execution requests from interfering with each other.
        """
        if subshell_id is not None:
            await self._require_feature("kernel subshells")
        async with self._exec_lock:
            # Validate transport state
            if not (self._session and self._kernel_id):
                raise RuntimeError("ServerTransport not started. Call start() first.")

            # Auto-reconnect WebSocket if connection was lost
            if self._ws is None or self._ws.closed:
                logger.debug("execute(): WSocket closed → reopening")
                await self.start()

            # Clear any stale messages from previous requests
            self._drain_inbox()

            req = build_execute_request(
                code,
                silent=silent,
                store_history=store_history,
                user_expressions=user_expressions,
                allow_stdin=allow_stdin,
                stop_on_error=stop_on_error,
                metadata=metadata,
                subshell_id=subshell_id,
            )
            req["channel"] = "shell"
            req.setdefault("buffers", [])
            req["header"]["session"] = self._client_session_id
            req["msg_type"] = req["header"].get("msg_type", "execute_request")
            request_id = req["header"]["msg_id"]
            request_queue = self._register_request(request_id)

            preview = (code or "").splitlines()[0][:120]
            logger.info(
                "execute(request_id=%s, timeout=%s) code=%r ...", request_id, timeout, preview
            )

            # Trigger pre-execution hooks for instrumentation/logging
            kernel_hooks.trigger_before_execute_hooks(code)

            try:
                await self._send_shell(req)
            except BaseException:
                self._unregister_request(request_id)
                raise

            state = ExecutionState(max_output_bytes=self.cfg.max_output_bytes)
            callbacks = OutputCallbackDispatcher(
                output_callback,
                state.snapshot,
                timeout=self.cfg.output_callback_timeout,
            )

            async def _finish_callbacks(result: ExecutionResult) -> None:
                callback_error = await callbacks.finish()
                callbacks.apply_to(result)
                if callback_error is not None:
                    kernel_hooks.trigger_on_error_hooks(callback_error)

            try:
                async for msg in self._collect_for_request_stream(
                    request_id,
                    queue=request_queue,
                    timeout=timeout,
                ):
                    kernel_hooks.trigger_output_hooks(msg)
                    if state.apply(msg):
                        callbacks.publish()

                res = state.result()
                res.request_id = request_id
                # Execution collection is complete. Stop routing late IOPub
                # frames to this bounded queue before waiting for a potentially
                # slow final callback, otherwise the WebSocket pump can stall.
                self._unregister_request(request_id)
                await _finish_callbacks(res)

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

            except asyncio.CancelledError:
                self._unregister_request(request_id)
                await callbacks.cancel()
                raise
            except KernelDisconnectedError as e:
                self._unregister_request(request_id)
                if e.partial_result is None:
                    e.partial_result = state.result()
                    e.partial_result.request_id = request_id
                    e.partial_result.outcome = "unknown"
                    e.partial_result.status = "error"
                await _finish_callbacks(e.partial_result)
                kernel_hooks.trigger_on_error_hooks(e)
                raise
            except TimeoutError as e:
                self._unregister_request(request_id)
                res = state.result()
                res.request_id = request_id
                res.status = "error"
                res.outcome = "unknown"
                res.timed_out = True
                res.stderr += f"\nExecution timed out after {timeout}s."
                await _finish_callbacks(res)
                kernel_hooks.trigger_on_error_hooks(e)
                return res
            except Exception as e:
                self._unregister_request(request_id)
                await callbacks.finish()
                # Trigger error hooks for consistent error handling
                kernel_hooks.trigger_on_error_hooks(e)
                logger.exception("execute(%s) failed: %s", request_id, e)
                raise
            finally:
                self._unregister_request(request_id)

    # ── Introspection / control ──────────────────────────────────────────

    async def restart(self) -> None:
        """Restart the remote kernel via the REST API.

        Issues ``POST /api/kernels/{kernel_id}/restart`` and waits for the
        server to confirm.  The existing kernel ID and WebSocket connection
        are preserved so the transport remains usable immediately.

        Raises:
            RuntimeError: If the transport is not started or the server
                returns an error status.
        """
        async with self._exec_lock:
            if not (self._session and self._kernel_id):
                raise RuntimeError("ServerTransport not started. Call start() first.")
            async with self._session.post(
                f"{self._base}/api/kernels/{self._kernel_id}/restart"
            ) as response:
                response.raise_for_status()
            await self._close_websocket()
            await self.start()
            await self._shell_request_locked(
                build_kernel_info_request(), timeout=self.cfg.startup_timeout
            )
            self._supported_features = None
            logger.info("Kernel restarted and ready (kernel_id=%s)", self._kernel_id)

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
        session: int = 0,
        start: int = 0,
        stop: int = 0,
        pattern: str = "",
        unique: bool = False,
    ) -> HistoryResult:
        """Retrieve execution history from the remote kernel."""
        reply = await self._shell_request(
            build_history_request(
                output=output,
                raw=raw,
                hist_access_type=hist_access_type,
                n=n,
                session=session,
                start=start,
                stop=stop,
                pattern=pattern,
                unique=unique,
            )
        )
        content = reply.get("content", {})
        entries = decode_history_entries(content.get("history", []))
        return HistoryResult(history=entries, status=content.get("status", "ok"))

    async def kernel_info(self) -> KernelInfoResult:
        """Retrieve metadata about the remote kernel."""
        reply = await self._shell_request(build_kernel_info_request())
        content = reply.get("content", {})
        lang = content.get("language_info", {})
        result = KernelInfoResult(
            protocol_version=content.get("protocol_version", ""),
            implementation=content.get("implementation", ""),
            implementation_version=content.get("implementation_version", ""),
            language_info=lang,
            banner=content.get("banner", ""),
            status=content.get("status", "ok"),
            help_links=content.get("help_links", []),
            supported_features=content.get("supported_features", []),
            raw_content=dict(content),
        )
        self._supported_features = set(result.supported_features)
        return result

    async def debug(self, request: dict) -> dict:
        """Send a DAP request over the control channel."""
        if not isinstance(request, dict):
            raise TypeError("debug request must be a dictionary")
        await self._require_feature("debugger")
        reply = await self._control_request(build_control_request("debug_request", request))
        return dict(reply.get("content") or {})

    async def create_subshell(self) -> str:
        """Create an advertised kernel subshell."""
        await self._require_feature("kernel subshells")
        reply = await self._control_request(build_control_request("create_subshell_request"))
        content = reply.get("content") or {}
        self._raise_control_error(content, "create subshell")
        subshell_id = content.get("subshell_id")
        if not isinstance(subshell_id, str) or not subshell_id:
            raise RuntimeError("Kernel returned no subshell id")
        return subshell_id

    async def delete_subshell(self, subshell_id: str) -> None:
        """Delete an advertised kernel subshell."""
        await self._require_feature("kernel subshells")
        reply = await self._control_request(
            build_control_request(
                "delete_subshell_request",
                {"subshell_id": subshell_id},
            )
        )
        self._raise_control_error(reply.get("content") or {}, "delete subshell")

    async def list_subshells(self) -> list[str]:
        """List advertised kernel subshells."""
        await self._require_feature("kernel subshells")
        reply = await self._control_request(build_control_request("list_subshell_request"))
        content = reply.get("content") or {}
        self._raise_control_error(content, "list subshells")
        ids = content.get("subshell_id", [])
        if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
            raise RuntimeError("Kernel returned invalid subshell ids")
        return ids

    async def _require_feature(self, feature: str) -> None:
        if self._supported_features is None:
            await self.kernel_info()
        if feature not in (self._supported_features or set()):
            raise UnsupportedKernelCapabilityError(
                f"Kernel does not advertise the {feature!r} capability"
            )

    async def _control_request(self, req: dict) -> dict:
        async with self._control_lock:
            return await self._channel_request_locked(
                req,
                channel="control",
                timeout=self.cfg.request_timeout,
            )

    @staticmethod
    def _raise_control_error(content: dict, operation: str) -> None:
        if content.get("status", "ok") != "ok":
            raise RuntimeError(f"Could not {operation}: {content.get('evalue', 'kernel error')}")

    async def _shell_request(self, req: dict) -> dict:
        """
        Send a shell-channel request and wait for the matching reply.

        This is the generic workhorse for all non-execute shell requests.
        It handles connection validation, inbox draining, request correlation,
        and timeout handling.
        """
        async with self._exec_lock:
            return await self._shell_request_locked(req, timeout=self.cfg.request_timeout)

    async def _shell_request_locked(self, req: dict, *, timeout: float) -> dict:
        return await self._channel_request_locked(req, channel="shell", timeout=timeout)

    async def _channel_request_locked(
        self,
        req: dict,
        *,
        channel: str,
        timeout: float,
    ) -> dict:
        if not (self._session and self._kernel_id):
            raise RuntimeError("ServerTransport not started. Call start() first.")
        if self._ws is None or self._ws.closed:
            await self.start()

        req["channel"] = channel
        req.setdefault("buffers", [])
        req["header"]["session"] = self._client_session_id
        req["msg_type"] = req["header"].get("msg_type", "unknown")
        request_id = req["header"]["msg_id"]
        queue = self._register_request(request_id)

        try:
            await self._send_shell(req)
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError(f"Timeout waiting for reply to {req['msg_type']}")
                try:
                    raw = await asyncio.wait_for(queue.get(), min(remaining, 0.5))
                except TimeoutError:
                    continue
                if raw.get("msg_type") == "__ws_closed__":
                    raise KernelDisconnectedError(
                        f"Kernel connection closed while waiting for {req['msg_type']}"
                    )
                msg = raw.get("msg") or raw
                header = msg.get("header") or {}
                parent = msg.get("parent_header") or {}
                msg_type = header.get("msg_type", "")
                parent_id = parent.get("msg_id")

                if parent_id == request_id and msg_type.endswith("_reply"):
                    return msg
        finally:
            self._unregister_request(request_id)

    async def _collect_for_request_stream(
        self,
        request_id: str,
        *,
        queue: asyncio.Queue,
        timeout: float | None,
    ):
        """
        Yield each kernel event for this request as it arrives (streaming).
        We only consider DONE after seeing BOTH:
        - iopub status: idle        with parent_header.msg_id == request_id
        - shell execute_reply       with parent_header.msg_id == request_id

        The WebSocket pump routes only frames with this request's parent ID to
        ``queue``; unmatched broadcasts remain in the transport inbox.
        """
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

            if raw.get("msg_type") == "__ws_closed__":
                raise KernelDisconnectedError("Kernel WebSocket closed before execution completed")

            # tolerate {"msg": {...}} envelopes
            msg = raw.get("msg") or raw

            header = msg.get("header") or {}
            parent_hdr = msg.get("parent_header") or {}
            content = msg.get("content") or {}

            msg_type = header.get("msg_type")
            parent_id = parent_hdr.get("msg_id")

            # Unparented broadcasts also belong outside this execution result.
            if parent_id != request_id:
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
        matches = [
            session for session in sessions if session.get("path") == path and session.get("kernel")
        ]
        if len(matches) > 1:
            ids = [session.get("id", "<unknown>") for session in matches]
            raise RuntimeError(f"Multiple active Jupyter sessions match notebook {path!r}: {ids}")
        return matches[0] if matches else None

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
        """Send a shell or control message through the kernel WebSocket."""
        assert self._ws is not None
        try:
            if msg.get("buffers"):
                await self._ws.send_bytes(serialize_binary_message(msg))
            else:
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
                        await self._dispatch_frame(msg.json())
                    except Exception:
                        # If parsing fails, skip this frame
                        continue
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    try:
                        await self._dispatch_frame(deserialize_binary_message(msg.data))
                    except Exception as exc:
                        ws_logger.warning("Invalid binary kernel frame: %s", exc)
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
            closed = {"msg_type": "__ws_closed__", "content": {}}
            for queue in [self._inbox, *list(self._request_queues.values())]:
                try:
                    if queue.full():
                        queue.get_nowait()
                    queue.put_nowait(closed)
                except Exception:
                    pass

    async def _dispatch_frame(self, frame: dict) -> None:
        """Route one kernel frame to its request without competing consumers."""
        inner = frame.get("msg") or frame
        parent = inner.get("parent_header") or frame.get("parent_header") or {}
        queue = self._request_queues.get(parent.get("msg_id"))
        if queue is None:
            if self._inbox.full():
                self._inbox.get_nowait()
            self._inbox.put_nowait(frame)
            return
        await queue.put(frame)

    def _register_request(self, request_id: str) -> asyncio.Queue:
        """Create the dedicated queue used by one in-flight request."""
        if request_id in self._request_queues:
            raise RuntimeError(f"Duplicate kernel request id: {request_id}")
        queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._request_queues[request_id] = queue
        return queue

    def _unregister_request(self, request_id: str) -> None:
        """Stop routing frames for a settled request."""
        queue = self._request_queues.pop(request_id, None)
        if queue is not None and queue.full():
            queue.get_nowait()

    def _drain_inbox(self) -> None:
        try:
            while True:
                self._inbox.get_nowait()
        except asyncio.QueueEmpty:
            pass

    async def _close_websocket(self) -> None:
        pump = self._pump_task
        self._pump_task = None
        if pump is not None:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
        if self._ws is not None:
            await self._ws.close()
        self._ws = None
