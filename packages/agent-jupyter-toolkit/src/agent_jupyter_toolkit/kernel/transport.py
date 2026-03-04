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
    - MUST call `output_callback(outputs_so_far, exec_count)` after each IOPub
        message that changes visible state (execute_input, stream, display_data,
        execute_result, clear_output, error), in order, if a callback is provided.
    - SHOULD also call it once at the end to deliver the final snapshot.
    """

    async def start(self) -> None:
        """Start or attach to a kernel and initialize channels."""
        ...

    async def shutdown(self) -> None:
        """Stop the kernel and teardown channels."""
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
        store_history: bool = True,
        allow_stdin: bool = False,
        stop_on_error: bool = True,
    ) -> ExecutionResult:
        """
        Execute code and (optionally) stream outputs via `output_callback`.

        Semantics:
        - If provided, `output_callback` is awaited *in order* after each IOPub message
            that changes the cell's visible state, passing the cumulative outputs and
            the latest execution_count (or None if not yet known).
        - `timeout` applies to the overall cell execution

        Call-order and shape guarantees:
        - Order: Calls to `output_callback` are strictly ordered as messages arrive.
        - Shape: `outputs` is nbformat-like (dicts with `output_type`, `data`, `metadata`, etc.).
            It represents the *current* state (e.g., after a `clear_output`, the list may become
            empty).
        - Count: `execution_count` may be `None` until the kernel emits `execute_input`.
        - Final snapshot: The callback is typically invoked again with the final set once
          the request is complete.
        """
        ...

    # ── Introspection / control ──────────────────────────────────────────

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
