"""
Local kernel transport for agent-jupyter-toolkit.

This module provides LocalTransport, which connects to local Jupyter kernels
via ZMQ (Zero Message Queue) for high-performance, low-latency communication.
The transport handles kernel lifecycle management, code execution, and real-time
output streaming for AI agent workflows.

Key features:
- Direct ZMQ communication with local kernels
- Support for existing connection files (kernel reuse)
- Real-time output streaming via callbacks
- Robust error handling and cleanup
- Full compatibility with Jupyter kernel protocol
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

from jupyter_core.paths import jupyter_runtime_dir

from ..callbacks import OutputCallbackDispatcher
from ..execution_state import ExecutionState
from ..history import decode_history_entries
from ..hooks import kernel_hooks
from ..manager import KernelManager
from ..transport import KernelTransport
from ..types import (
    CompleteResult,
    ExecutionResult,
    HistoryResult,
    InspectResult,
    IsCompleteResult,
    KernelInfoResult,
    UnsupportedKernelCapabilityError,
)


class LocalTransport(KernelTransport):
    """
    Local kernel transport using ZMQ for direct communication.

    This transport manages local Jupyter kernel processes and provides
    high-performance execution capabilities for AI agents. It supports
    both fresh kernel creation and attachment to existing kernels via
    connection files.

    Architecture:
        LocalTransport -> KernelManager -> AsyncKernelManager (jupyter_client)
                       -> Direct ZMQ communication with execute_interactive

    Lifecycle:
        1. start(): Launch or attach to kernel, initialize ZMQ channels
        2. execute(): Send code, stream outputs, return results
        3. shutdown(): Clean kernel termination and resource cleanup
    """

    def __init__(
        self,
        *,
        kernel_name: str = "python3",
        connection_file_name: str | None = None,
        packer: str | None = None,
        startup_timeout: float = 60.0,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        kernel_args: list[str] | None = None,
        max_output_bytes: int | None = 50 * 1024 * 1024,
        output_callback_timeout: float | None = 30.0,
        transport_encryption: str = "disabled",
        manager_factory: Callable[..., KernelManager] | None = None,
    ) -> None:
        """
        Initialize local transport with kernel configuration.

        Args:
            kernel_name: Jupyter kernel specification name (e.g., "python3", "ir", "julia-1.8").
                        Must be installed and available via `jupyter kernelspec list`.
            connection_file_name: Optional path to existing kernel connection file.
                                If provided, attempts to attach to running kernel instead
                                of launching new one. Useful for kernel reuse patterns.
            packer: Optional serializer name for jupyter_client.Session (e.g., "json", "orjson").

        Note:
            Connection files are typically in jupyter_runtime_dir() and follow
            format: kernel-{uuid}.json containing ZMQ port and key information.
        """
        if connection_file_name and (
            cwd is not None or env is not None or kernel_args or transport_encryption != "disabled"
        ):
            raise ValueError(
                "cwd, env, kernel_args, and transport_encryption apply only when "
                "launching a new kernel"
            )

        # Initialize kernel manager with configuration
        manager_type = manager_factory or KernelManager
        self._km = manager_type(
            kernel_name=kernel_name,
            connection_file_name=connection_file_name,
            packer=packer,
            startup_timeout=startup_timeout,
            cwd=cwd,
            env=env,
            kernel_args=kernel_args,
            transport_encryption=transport_encryption,
        )
        self._request_lock = asyncio.Lock()
        self._control_lock = asyncio.Lock()
        self._max_output_bytes = max_output_bytes
        self._output_callback_timeout = output_callback_timeout
        self._supported_features: set[str] | None = None

    @property
    def kernel_manager(self) -> KernelManager:
        """
        Access to underlying kernel manager for advanced operations.

        Returns:
            KernelManager: The internal kernel manager instance.

        Use cases:
            - Accessing connection file paths
            - Advanced kernel introspection
            - Custom kernel management operations
            - Debugging kernel state

        Example:
            >>> transport = LocalTransport()
            >>> await transport.start()
            >>> km = transport.kernel_manager
            >>> print(f"Kernel ID: {km.kernel_id}")
        """
        return self._km

    async def start(self) -> None:
        """
        Start or attach to a local Jupyter kernel for code execution.

        This method implements smart connection logic:
        1. If connection_file_name was provided in __init__, try to attach first
        2. If connection file exists and is valid, reuse that kernel
        3. Otherwise, launch a fresh kernel process
        4. Ready for direct code execution via execute() method

        Connection file logic:
            - Relative paths are resolved against jupyter_runtime_dir()
            - Connection files contain ZMQ ports, keys, and transport info
            - Attachment allows kernel reuse across agent sessions

        Raises:
            RuntimeError: If kernel fails to start or become ready
            FileNotFoundError: If specified connection file is invalid

        Note:
            This method is idempotent - calling multiple times is safe
            (though not recommended due to potential resource leaks).
        """
        # Smart connection logic: try existing connection file first
        cf_name = getattr(self._km, "_connection_file_name", None)
        if cf_name:
            # Handle relative vs absolute paths
            cf_path = cf_name
            if not os.path.isabs(cf_path):
                # Resolve relative paths against Jupyter runtime directory
                cf_path = os.path.join(jupyter_runtime_dir(), cf_path)

            # An explicit attachment must never create a replacement kernel.
            if os.path.exists(cf_path):
                await self._km.connect_to_existing(cf_path)
            else:
                raise FileNotFoundError(f"Kernel connection file does not exist: {cf_path}")
        else:
            # No connection file specified, always start fresh
            await self._km.start()

        # Kernel is ready for direct execution
        self._supported_features = None

    async def shutdown(self) -> None:
        """Close channels and terminate only a kernel created by this client."""
        await self._km.shutdown()

    async def shutdown_kernel(self) -> None:
        """Explicitly terminate the kernel, including an attached kernel."""
        await self._km.shutdown(force_kernel=True)

    async def is_alive(self) -> bool:
        """
        Check if the local kernel process is alive and responsive.

        Returns:
            bool: True if kernel is running and can accept requests,
                  False if kernel is dead, crashed, or unresponsive.

        Note:
            This method checks actual kernel process status, not just
            transport connectivity. A False result indicates the kernel
            needs to be restarted via start().
        """
        return await self._km.is_alive()

    async def execute(
        self,
        code: str,
        *,
        timeout: float | None = None,
        output_callback=None,
        silent: bool = False,
        store_history: bool = True,
        user_expressions: dict | None = None,
        metadata: dict | None = None,
        subshell_id: str | None = None,
        allow_stdin: bool = False,
        stop_on_error: bool = True,
    ) -> ExecutionResult:
        """Execute while ensuring one upstream helper owns the client channels."""
        if subshell_id is not None:
            await self._require_feature("kernel subshells")
        async with self._request_lock:
            return await self._execute_locked(
                code,
                timeout=timeout,
                output_callback=output_callback,
                silent=silent,
                store_history=store_history,
                user_expressions=user_expressions,
                metadata=metadata,
                subshell_id=subshell_id,
                allow_stdin=allow_stdin,
                stop_on_error=stop_on_error,
            )

    async def _execute_locked(
        self,
        code: str,
        *,
        timeout: float | None = None,
        output_callback=None,
        silent: bool = False,
        store_history: bool = True,
        user_expressions: dict | None = None,
        metadata: dict | None = None,
        subshell_id: str | None = None,
        allow_stdin: bool = False,
        stop_on_error: bool = True,
    ) -> ExecutionResult:
        """
        Execute code in the local kernel with real-time output streaming.

        This method provides comprehensive code execution with:
        - Real-time output callbacks for responsive UIs
        - Configurable timeout handling
        - History and stdin control
        - Error handling options

        Args:
            code: Python code to execute. Can be multi-line.
            timeout: Maximum execution time in seconds. None = no client timeout
                    (kernel may still have its own timeout).
            output_callback: Optional async callback for real-time outputs.
                           Signature: callback(outputs: List[Dict], execution_count: Optional[int])
                           Called whenever new output arrives from kernel.
            store_history: If True, code is stored in kernel's input history.
                          Disable for utility code that shouldn't pollute history.
            allow_stdin: If True, kernel can request user input via stdin.
                        Should be False for headless/agent usage.
            stop_on_error: If True, kernel stops processing on first error.
                          If False, continues execution despite errors.

        Returns:
            ExecutionResult: Complete execution information including:
                - status: "ok", "error", or "abort"
                - execution_count: Kernel's execution counter
                - outputs: List of display outputs (text, images, etc.)
                - error_message: Description if execution failed

        Raises:
            RuntimeError: If transport hasn't been started via start()
            asyncio.TimeoutError: If execution exceeds timeout

        Example:
            >>> result = await transport.execute("print('Hello')")
            >>> print(result.status)  # "ok"
            >>> print(result.outputs)  # [{'name': 'stdout', 'text': 'Hello\\n'}]
        """
        # Validate transport state
        if not self._km or not self._km.client:
            raise RuntimeError("LocalTransport not started. Call start() first.")

        kc = self._km.client
        state = ExecutionState(max_output_bytes=self._max_output_bytes)

        # Trigger pre-execution hooks for instrumentation/logging
        kernel_hooks.trigger_before_execute_hooks(code)

        callbacks = OutputCallbackDispatcher(
            output_callback,
            state.snapshot,
            timeout=self._output_callback_timeout,
        )
        completed_request = False

        # Custom output hook to capture outputs for our result object
        def output_hook(msg: dict[str, Any]) -> None:
            """Capture IOPub messages and fold them into our ExecutionResult."""
            kernel_hooks.trigger_output_hooks(msg)
            if state.apply(msg):
                callbacks.publish()

        try:
            original_execute = kc.execute
            if metadata or subshell_id is not None:

                def _execute_with_metadata(
                    source,
                    silent=False,
                    store_history=True,
                    user_expressions=None,
                    allow_stdin=None,
                    stop_on_error=True,
                ):
                    content = {
                        "code": source,
                        "silent": silent,
                        "store_history": store_history,
                        "user_expressions": user_expressions or {},
                        "allow_stdin": bool(allow_stdin),
                        "stop_on_error": stop_on_error,
                    }
                    message = kc.session.msg("execute_request", content=content, metadata=metadata)
                    if subshell_id is not None:
                        message["header"]["subshell_id"] = subshell_id
                    kc.shell_channel.send(message)
                    return message["header"]["msg_id"]

                kc.execute = _execute_with_metadata
            try:
                reply = await kc.execute_interactive(
                    code,
                    silent=silent,
                    store_history=store_history,
                    user_expressions=user_expressions,
                    allow_stdin=allow_stdin,
                    stop_on_error=stop_on_error,
                    timeout=timeout,
                    output_hook=output_hook,
                )
            finally:
                if metadata or subshell_id is not None:
                    kc.execute = original_execute
            state.apply({"msg_type": "execute_reply", "content": reply.get("content", {})})
            res = state.result()
            res.request_id = (reply.get("parent_header") or {}).get("msg_id")
            completed_request = True

        except asyncio.CancelledError:
            await callbacks.cancel()
            raise

        except TimeoutError as te:
            res = state.result()
            res.status = "error"
            res.outcome = "unknown"
            res.timed_out = True
            res.stderr += f"\nExecution timed out after {timeout}s."
            kernel_hooks.trigger_on_error_hooks(te)

        except Exception as e:
            res = state.result()
            res.status = "error"
            res.outcome = "unknown"
            res.stderr += f"\n{type(e).__name__}: {e}"
            kernel_hooks.trigger_on_error_hooks(e)
        finally:
            callback_error = await callbacks.finish()

        if callback_error is not None:
            kernel_hooks.trigger_on_error_hooks(callback_error)
        callbacks.apply_to(res)
        if completed_request:
            kernel_hooks.trigger_after_execute_hooks(res)

        return res

    # ── Introspection / control ──────────────────────────────────────────

    async def restart(self) -> None:
        """Restart the local kernel process via the KernelManager.

        The kernel is restarted in-place — its OS process is replaced but the
        ZMQ channels and connection file are preserved.  After this call the
        kernel has a clean namespace and is ready for new executions.

        Raises:
            RuntimeError: If no kernel is currently managed.
        """
        async with self._request_lock:
            await self._km.restart()
            self._supported_features = None

    async def interrupt(self) -> None:
        """Interrupt the running kernel via the KernelManager."""
        await self._km.interrupt()

    async def complete(self, code: str, cursor_pos: int) -> CompleteResult:
        """Request tab-completion from the kernel."""
        reply = await self._client_reply("complete", code, cursor_pos)
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
        """Inspect an object at the cursor position."""
        reply = await self._client_reply("inspect", code, cursor_pos, detail_level=detail_level)
        content = reply.get("content", {})
        return InspectResult(
            found=content.get("found", False),
            data=content.get("data", {}),
            metadata=content.get("metadata", {}),
            status=content.get("status", "ok"),
        )

    async def is_complete(self, code: str) -> IsCompleteResult:
        """Check whether *code* is syntactically complete."""
        reply = await self._client_reply("is_complete", code)
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
        """Retrieve execution history from the kernel."""
        reply = await self._client_reply(
            "history",
            raw=raw,
            output=output,
            hist_access_type=hist_access_type,
            n=n,
            session=session,
            start=start,
            stop=stop,
            pattern=pattern,
            unique=unique,
        )
        content = reply.get("content", {})
        entries = decode_history_entries(content.get("history", []))
        return HistoryResult(history=entries, status=content.get("status", "ok"))

    async def kernel_info(self) -> KernelInfoResult:
        """Retrieve metadata about the connected kernel."""
        reply = await self._client_reply("kernel_info")
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
        reply = await self._control_reply("debug_request", request)
        return dict(reply.get("content") or {})

    async def create_subshell(self) -> str:
        """Create an advertised kernel subshell."""
        await self._require_feature("kernel subshells")
        content = (await self._control_reply("create_subshell_request")).get("content") or {}
        self._raise_control_error(content, "create subshell")
        subshell_id = content.get("subshell_id")
        if not isinstance(subshell_id, str) or not subshell_id:
            raise RuntimeError("Kernel returned no subshell id")
        return subshell_id

    async def delete_subshell(self, subshell_id: str) -> None:
        """Delete an advertised kernel subshell."""
        await self._require_feature("kernel subshells")
        content = (
            await self._control_reply(
                "delete_subshell_request",
                {"subshell_id": subshell_id},
            )
        ).get("content") or {}
        self._raise_control_error(content, "delete subshell")

    async def list_subshells(self) -> list[str]:
        """List advertised kernel subshells."""
        await self._require_feature("kernel subshells")
        content = (await self._control_reply("list_subshell_request")).get("content") or {}
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

    async def _control_reply(
        self,
        msg_type: str,
        content: dict | None = None,
    ) -> dict[str, Any]:
        async with self._control_lock:
            if not self._km or not self._km.client:
                raise RuntimeError("LocalTransport not started. Call start() first.")
            client = self._km.client
            message = client.session.msg(msg_type, content=content or {})
            client.control_channel.send(message)
            return await client._recv_reply(
                message["header"]["msg_id"],
                timeout=10,
                channel="control",
            )

    @staticmethod
    def _raise_control_error(content: dict, operation: str) -> None:
        if content.get("status", "ok") != "ok":
            raise RuntimeError(f"Could not {operation}: {content.get('evalue', 'kernel error')}")

    async def _client_reply(self, method: str, *args, **kwargs) -> dict[str, Any]:
        """Call one reply-consuming jupyter_client helper under the request guard."""
        async with self._request_lock:
            if not self._km or not self._km.client:
                raise RuntimeError("LocalTransport not started. Call start() first.")
            operation = getattr(self._km.client, method)
            return await operation(*args, **kwargs, reply=True, timeout=10)
