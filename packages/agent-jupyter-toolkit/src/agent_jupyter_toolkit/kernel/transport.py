"""
KernelTransport protocol for pluggable execution backends.

Local (ZMQ) and Server (HTTP+WS) transports both implement this interface.
"""

from __future__ import annotations

from .types import (
    CompleteResult,
    ExecutionResult,
    HistoryResult,
    InspectResult,
    IsCompleteResult,
    KernelInfoResult,
    OutputCallback,
)


class KernelTransport:
    """
    Minimal async interface for executing code in a Jupyter kernel.

    Implementations:
    - MUST deliver ordered, cumulative snapshots to ``output_callback`` without
        blocking kernel message collection. Implementations may coalesce pending
        snapshots when the callback is slower than the kernel.
    - MUST attempt one final snapshot and report callback delivery diagnostics on
        ``ExecutionResult`` without changing the kernel execution status.
    """

    async def start(self) -> None:
        """Start or attach to a kernel and initialize channels."""
        ...

    async def shutdown(self) -> None:
        """Tear down channels and stop any kernel owned by this client."""
        ...

    async def shutdown_kernel(self) -> None:
        """Explicitly terminate the kernel, even when this client borrowed it."""
        ...

    async def is_alive(self) -> bool:
        """Return True if the kernel is alive."""
        ...

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
        Execute code and (optionally) stream outputs via `output_callback`.

        Semantics:
        - If provided, ``output_callback`` receives ordered cumulative snapshots.
            Pending snapshots may be coalesced when the callback is slower than
            message intake; the newest state and final state are retained.
        - `timeout` applies to the overall cell execution

        Call-order and shape guarantees:
        - Order: Delivered calls are strictly ordered as messages arrive.
        - Shape: `outputs` is nbformat-like (dicts with `output_type`, `data`, `metadata`, etc.).
            It represents the *current* state (e.g., after a `clear_output`, the list may become
            empty).
        - Count: `execution_count` may be `None` until the kernel emits `execute_input`.
        - Final snapshot: A final delivery is attempted when the request completes.
        - Diagnostics: Callback timeout/failure is reported separately through
          ``callback_status``, ``callback_error``, and
          ``callback_snapshots_coalesced``.
        """
        ...

    async def debug(self, request: dict) -> dict:
        """Send a Debug Adapter Protocol request to a capable kernel."""
        raise NotImplementedError

    async def create_subshell(self) -> str:
        """Create a kernel subshell and return its stable ID."""
        raise NotImplementedError

    async def delete_subshell(self, subshell_id: str) -> None:
        """Delete a kernel subshell."""
        raise NotImplementedError

    async def list_subshells(self) -> list[str]:
        """Return the IDs of active kernel subshells."""
        raise NotImplementedError

    # ── Introspection / control ──────────────────────────────────────────

    async def restart(self) -> None:
        """
        Restart the kernel, preserving the connection.

        After restart the kernel has a fresh namespace but the transport
        remains usable — callers do **not** need to call ``start()`` again.

        For local kernels this restarts the OS process; for remote kernels
        this issues a ``POST /api/kernels/{id}/restart`` request.

        Raises:
            RuntimeError: If the transport is not started or the restart fails.
        """
        raise NotImplementedError

    async def interrupt(self) -> None:
        """
        Interrupt the currently running execution in the kernel.

        Sends a SIGINT (or platform-equivalent) to the kernel process. Use this
        to cancel long-running or hung computations without killing the kernel.
        """
        raise NotImplementedError

    async def complete(self, code: str, cursor_pos: int) -> CompleteResult:
        """
        Request tab-completion suggestions from the kernel.

        Args:
            code: The code context for completion.
            cursor_pos: Unicode offset within *code* where the cursor is.

        Returns:
            CompleteResult with matches and cursor span.
        """
        raise NotImplementedError

    async def inspect(
        self,
        code: str,
        cursor_pos: int,
        detail_level: int = 0,
    ) -> InspectResult:
        """
        Inspect an object at the cursor position for documentation/signature.

        Args:
            code: The code context containing the object.
            cursor_pos: Unicode offset within *code*.
            detail_level: 0 for summary, 1 for full docs.

        Returns:
            InspectResult with MIME-keyed documentation.
        """
        raise NotImplementedError

    async def is_complete(self, code: str) -> IsCompleteResult:
        """
        Ask the kernel whether *code* is syntactically complete.

        Args:
            code: Source fragment to check.

        Returns:
            IsCompleteResult with status ("complete", "incomplete", "invalid", "unknown")
            and optional indentation hint.
        """
        raise NotImplementedError

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
        """
        Retrieve execution history from the kernel.

        Args:
            output: Include output alongside input.
            raw: Return raw (un-transformed) input.
            hist_access_type: "range", "tail", or "search".
            n: Number of entries (for "tail") or max results (for "search").

        Returns:
            HistoryResult containing a list of HistoryEntry items.
        """
        raise NotImplementedError

    async def kernel_info(self) -> KernelInfoResult:
        """
        Retrieve metadata about the connected kernel.

        Returns:
            KernelInfoResult with protocol version, language info, implementation
            details, and banner text.
        """
        raise NotImplementedError
