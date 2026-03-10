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

import os
from typing import Any

from jupyter_core.paths import jupyter_runtime_dir

from ..hooks import kernel_hooks
from ..manager import KernelManager
from ..transport import KernelTransport
from ..types import (
    CompleteResult,
    ExecutionResult,
    HistoryEntry,
    HistoryResult,
    InspectResult,
    IsCompleteResult,
    KernelInfoResult,
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
        # Initialize kernel manager with configuration
        self._km = KernelManager(
            kernel_name=kernel_name,
            connection_file_name=connection_file_name,
            packer=packer,
        )

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

            # Prefer existing kernel if connection file exists
            if os.path.exists(cf_path):
                await self._km.connect_to_existing(cf_path)
            else:
                # Connection file not found, start fresh kernel
                await self._km.start()
        else:
            # No connection file specified, always start fresh
            await self._km.start()

        # Kernel is ready for direct execution

    async def shutdown(self) -> None:
        """
        Shut down the local kernel and release all resources.

        This method performs clean shutdown:
        1. Terminates kernel process gracefully
        2. Closes ZMQ connections
        3. Releases system resources

        The shutdown is fault-tolerant - it will attempt cleanup
        even if the kernel is already dead or unresponsive.

        Note:
            After shutdown, this transport cannot be reused.
            Create a new LocalTransport instance for further operations.
        """
        # Attempt graceful kernel shutdown
        await self._km.shutdown()

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
        store_history: bool = True,
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
        res = ExecutionResult(status="ok")

        # Trigger pre-execution hooks for instrumentation/logging
        kernel_hooks.trigger_before_execute_hooks(code)

        # Accumulators for output_callback
        outputs: list[dict[str, Any]] = []
        exec_count: int | None = None

        # Queue of pending callback snapshots.  The synchronous output_hook
        # (called from execute_interactive's ZMQ loop) cannot await coroutines,
        # so we enqueue snapshots and drain them *in order* after execute_interactive
        # returns.  This satisfies the KernelTransport contract that callbacks are
        # awaited strictly in arrival order.
        pending_callbacks: list[tuple[list[dict[str, Any]], int | None]] = []

        def _enqueue_callback() -> None:
            """Snapshot current outputs and enqueue for later awaiting."""
            if output_callback:
                pending_callbacks.append((outputs[:], exec_count))

        async def _flush_callbacks() -> None:
            """Drain queued snapshots in arrival order and emit final snapshot."""
            if not output_callback:
                pending_callbacks.clear()
                return
            for snapshot_outputs, snapshot_count in pending_callbacks:
                await output_callback(snapshot_outputs, snapshot_count)
            pending_callbacks.clear()
            await output_callback(outputs[:], exec_count)

        # Custom output hook to capture outputs for our result object
        def output_hook(msg: dict[str, Any]) -> None:
            """Capture IOPub messages and fold them into our ExecutionResult."""
            nonlocal exec_count
            kernel_hooks.trigger_output_hooks(msg)

            header = msg.get("header") or {}
            msg_type = header.get("msg_type")
            content = msg.get("content") or {}

            if msg_type == "execute_input":
                ec = content.get("execution_count")
                if ec is not None:
                    res.execution_count = ec
                    exec_count = ec
                _enqueue_callback()

            elif msg_type == "stream":
                name = content.get("name")
                text = content.get("text", "") or ""
                output_dict = {"output_type": "stream", "name": name, "text": text}
                res.outputs.append(output_dict)
                outputs.append(output_dict)
                if name == "stdout":
                    res.stdout += text
                elif name == "stderr":
                    res.stderr += text
                _enqueue_callback()

            elif msg_type in ("display_data", "update_display_data", "execute_result"):
                data = content.get("data") or {}
                md = content.get("metadata") or {}
                if content.get("execution_count") is not None:
                    res.execution_count = content["execution_count"]
                    exec_count = res.execution_count
                out: dict[str, Any] = {
                    "output_type": (
                        "execute_result" if msg_type == "execute_result" else "display_data"
                    ),
                    "data": data,
                    "metadata": md,
                }
                if out["output_type"] == "execute_result":
                    out["execution_count"] = res.execution_count
                res.outputs.append(out)
                outputs.append(out)
                _enqueue_callback()

            elif msg_type == "clear_output":
                res.outputs.clear()
                res.stdout = ""
                res.stderr = ""
                outputs.clear()
                _enqueue_callback()

            elif msg_type == "error":
                res.status = "error"
                err = {
                    "output_type": "error",
                    "ename": content.get("ename"),
                    "evalue": content.get("evalue"),
                    "traceback": content.get("traceback"),
                }
                res.outputs.append(err)
                outputs.append(err)
                _enqueue_callback()

        try:
            # Use execute_interactive for proper async handling
            reply = await kc.execute_interactive(
                code,
                silent=False,
                store_history=store_history,
                allow_stdin=allow_stdin,
                stop_on_error=stop_on_error,
                timeout=timeout,
                output_hook=output_hook,
            )

            # Update final status from reply (unless already set to error by IOPub)
            if res.status != "error":
                res.status = reply.get("content", {}).get("status", "ok")

            # Extract final execution count from reply
            if "execution_count" in reply.get("content", {}):
                res.execution_count = reply["content"]["execution_count"]
                exec_count = res.execution_count

            # Trigger post-execution hooks for instrumentation/cleanup
            kernel_hooks.trigger_after_execute_hooks(res)

        except TimeoutError as te:
            # Handle execution timeout gracefully
            res.status = "error"
            res.stderr += f"\nExecution timed out after {timeout}s."
            kernel_hooks.trigger_on_error_hooks(te)

        except Exception as e:
            # Handle any other execution errors
            res.status = "error"
            res.stderr += f"\n{type(e).__name__}: {e}"
            kernel_hooks.trigger_on_error_hooks(e)

        # Always flush callbacks (including timeout/error paths) so consumers
        # receive ordered snapshots and a final authoritative state.
        try:
            await _flush_callbacks()
        except Exception as cb_exc:
            res.status = "error"
            res.stderr += f"\n{type(cb_exc).__name__}: {cb_exc}"
            kernel_hooks.trigger_on_error_hooks(cb_exc)

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
        await self._km.restart()

    async def interrupt(self) -> None:
        """Interrupt the running kernel via the KernelManager."""
        await self._km.interrupt()

    async def complete(self, code: str, cursor_pos: int) -> CompleteResult:
        """Request tab-completion from the kernel."""
        if not self._km or not self._km.client:
            raise RuntimeError("LocalTransport not started. Call start() first.")
        kc = self._km.client
        reply = await kc.complete(code, cursor_pos, reply=True, timeout=10)
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
        if not self._km or not self._km.client:
            raise RuntimeError("LocalTransport not started. Call start() first.")
        kc = self._km.client
        reply = await kc.inspect(
            code, cursor_pos, detail_level=detail_level, reply=True, timeout=10
        )
        content = reply.get("content", {})
        return InspectResult(
            found=content.get("found", False),
            data=content.get("data", {}),
            metadata=content.get("metadata", {}),
            status=content.get("status", "ok"),
        )

    async def is_complete(self, code: str) -> IsCompleteResult:
        """Check whether *code* is syntactically complete."""
        if not self._km or not self._km.client:
            raise RuntimeError("LocalTransport not started. Call start() first.")
        kc = self._km.client
        reply = await kc.is_complete(code, reply=True, timeout=10)
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
        """Retrieve execution history from the kernel."""
        if not self._km or not self._km.client:
            raise RuntimeError("LocalTransport not started. Call start() first.")
        kc = self._km.client
        reply = await kc.history(
            raw=raw,
            output=output,
            hist_access_type=hist_access_type,
            n=n,
            reply=True,
            timeout=10,
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
        """Retrieve metadata about the connected kernel."""
        if not self._km or not self._km.client:
            raise RuntimeError("LocalTransport not started. Call start() first.")
        kc = self._km.client
        reply = await kc.kernel_info(reply=True, timeout=10)
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
