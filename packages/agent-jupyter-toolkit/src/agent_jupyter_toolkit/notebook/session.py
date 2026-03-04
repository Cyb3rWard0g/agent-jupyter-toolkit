from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

from ..kernel import ExecutionResult
from ..kernel import Session as KernelSession
from .transport import NotebookDocumentTransport
from .utils import to_nbformat_outputs

logger = logging.getLogger(__name__)


@dataclass
class NotebookSession:
    """
    High-level session that combines kernel execution with notebook document persistence.

    This class orchestrates the interaction between a kernel session (for code execution)
    and a notebook document transport (for persistence). It provides:

    - **Lifecycle Management**: Intelligent start/stop with component state awareness
    - **Execution with Streaming**: Real-time output mirroring to document as code executes
    - **Cell Operations**: Append, insert, run, and manage cells
    - **Error Resilience**: Graceful handling of timeouts and execution failures

    Example:
        ```python
        # Local file notebook
        from agent_jupyter_toolkit.kernel import create_session, SessionConfig
        from agent_jupyter_toolkit.notebook import make_document_transport, NotebookSession

        kernel_session = create_session(SessionConfig(mode="local"))
        doc_transport = make_document_transport("local", local_path="my_notebook.ipynb")

        notebook_session = NotebookSession(kernel=kernel_session, doc=doc_transport)

        async with notebook_session:
            idx, result = await notebook_session.append_and_run("print('Hello, World!')")
            print(f"Cell {idx} executed with status: {result.status}")
        ```

    Streaming Behavior:
        When executing cells, outputs are streamed to the document in real-time:
        - Each output message updates the cell immediately
        - Execution count changes are propagated
        - Clear output commands clear the cell outputs
        - Final write reconciles any differences using normalized nbformat
    """

    kernel: KernelSession
    doc: NotebookDocumentTransport
    _started: bool = field(default=False, init=False, repr=False)

    # --------------------------------------------------------------------- lifecycle

    async def __aenter__(self) -> NotebookSession:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def start(self) -> None:
        """
        Start kernel then document transport (idempotent).
        If `doc.start()` fails, the kernel is shut down to avoid leaks.
        """
        if self._started:
            logger.debug("NotebookSession already started, skipping")
            return

        try:
            # Start kernel (idempotent - kernel transport handles duplicate starts)
            logger.debug("Starting kernel session")
            await self.kernel.start()

            # Start document transport (idempotent - transport handles duplicate starts)
            logger.debug("Starting document transport")
            await self.doc.start()

            self._started = True
            logger.debug("NotebookSession started successfully")
        except Exception:
            # If anything fails, ensure kernel is shut down to avoid leaks
            self._started = False  # Make sure we reset the flag
            with contextlib.suppress(Exception):
                await self.kernel.shutdown()
            raise

    async def stop(self) -> None:
        """
        Stop document transport then kernel (idempotent, fault-tolerant).
        Always attempts to shut down the kernel, even if not fully started here.
        """
        if not self._started:
            with contextlib.suppress(Exception):
                await self.kernel.shutdown()
            return
        try:
            with contextlib.suppress(Exception):
                await self.doc.stop()
        finally:
            with contextlib.suppress(Exception):
                await self.kernel.shutdown()
            self._started = False

    async def _execute_with_streaming(
        self,
        code: str,
        cell_index: int,
        timeout: float | None = None,
    ) -> ExecutionResult:
        """
        Execute code with real-time output streaming to the document.

        This helper centralizes the streaming execution logic used by both
        append_and_run and run_at methods.

        Args:
            code: Source code to execute
            cell_index: Index of the cell being executed
            timeout: Optional execution timeout

        Returns:
            ExecutionResult: Complete execution result
        """
        # Accumulators for streaming updates
        accum: list[dict[str, Any]] = []
        exec_count: int | None = None

        # Fire-and-forget flush for responsiveness
        async def _flush_full() -> None:
            try:
                await self.doc.update_cell_outputs(cell_index, list(accum), exec_count)
            except Exception as e:
                logger.debug("Failed to flush outputs for cell %d: %s", cell_index, e)

        async def _flush_delta(
            updated_indices: set[int] | None = None, *, cleared: bool = False
        ) -> None:
            updater = getattr(self.doc, "update_cell_outputs_delta", None)
            if callable(updater):
                try:
                    await updater(
                        cell_index,
                        list(accum),
                        exec_count,
                        updated_indices=updated_indices,
                        cleared=cleared,
                    )
                    return
                except Exception as e:
                    logger.debug("Failed to flush delta outputs for cell %d: %s", cell_index, e)
            await _flush_full()

        # Define streaming callbacks
        def _on_output(out: dict[str, Any]) -> None:
            accum.append(out)
            idx = len(accum) - 1
            asyncio.create_task(_flush_delta({idx}))

        def _on_exec_count(n: int | None) -> None:
            nonlocal exec_count
            exec_count = n
            asyncio.create_task(_flush_delta())

        def _on_clear(wait: bool) -> None:
            accum.clear()
            asyncio.create_task(_flush_delta(set(), cleared=True))

        # Execute with hooks (preferred) or legacy callback fallback
        res: ExecutionResult | None = None
        try:
            if timeout is None:
                try:
                    res = await self.kernel.execute(
                        code,
                        on_output=_on_output,
                        on_exec_count=_on_exec_count,
                        on_clear_output=_on_clear,
                    )
                except TypeError:
                    # Fallback path: kernel expects a single snapshot callback
                    async def _legacy_cb(outputs, execution_count):
                        nonlocal accum, exec_count
                        accum = list(outputs or [])
                        exec_count = execution_count
                        await _flush_full()

                    res = await self.kernel.execute(
                        code,
                        output_callback=_legacy_cb,  # type: ignore[arg-type]
                    )
            else:
                try:
                    res = await asyncio.wait_for(
                        self.kernel.execute(
                            code,
                            on_output=_on_output,
                            on_exec_count=_on_exec_count,
                            on_clear_output=_on_clear,
                        ),
                        timeout=timeout,
                    )
                except TypeError:

                    async def _legacy_cb(outputs, execution_count):
                        nonlocal accum, exec_count
                        accum = list(outputs or [])
                        exec_count = execution_count
                        await _flush_full()

                    res = await asyncio.wait_for(
                        self.kernel.execute(
                            code,
                            output_callback=_legacy_cb,  # type: ignore[arg-type]
                        ),
                        timeout=timeout,
                    )
        finally:
            # Final authoritative write with normalized outputs
            try:
                if res is not None:
                    outs = to_nbformat_outputs(res) or []
                    final_count = getattr(res, "execution_count", None)
                    await self.doc.update_cell_outputs(cell_index, outs, final_count)
                    logger.debug(
                        f"[_execute_with_streaming] Kernel execution result: {res.__dict__}"
                    )
                    logger.debug(f"[_execute_with_streaming] Set outputs for cell {cell_index}")
                else:
                    # Execution failed - use accumulated outputs
                    await self.doc.update_cell_outputs(cell_index, list(accum), exec_count)
                    logger.debug(
                        f"[_execute_with_streaming] Using accumulated outputs for cell "
                        f"{cell_index} (execution failed)"
                    )
            except Exception as e:
                logger.warning(
                    f"[_execute_with_streaming] Failed to update cell outputs for {cell_index}: {e}"
                )

        # Return result or create error result for failed execution
        return (
            res
            if res is not None
            else ExecutionResult(status="error", stderr="Execution failed or timed out")
        )

    async def is_connected(self) -> bool:
        """True if both kernel and document transports are live."""
        return (await self.kernel.is_alive()) and (await self.doc.is_connected())

    # --------------------------------------------------------------------- cell reads

    async def get_cell(self, index: int) -> dict[str, Any]:
        """Return the cell at *index* as an nbformat-like dict.

        Delegates to the document transport.  For Yjs-backed transports
        this reads a single CRDT map; for file/HTTP-backed transports it
        may involve a full notebook fetch internally.

        Args:
            index: Zero-based cell index.

        Returns:
            Dict with ``cell_type``, ``source``, ``metadata``, and (for code
            cells) ``outputs`` and ``execution_count``.

        Raises:
            IndexError: if index is not in [0..len-1].
        """
        return await self.doc.get_cell(index)

    async def cell_count(self) -> int:
        """Return the number of cells in the notebook."""
        return await self.doc.cell_count()

    async def get_cell_source(self, index: int) -> str:
        """Return the source text of the cell at *index*."""
        return await self.doc.get_cell_source(index)

    async def append_and_run(
        self,
        code: str,
        *,
        metadata: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, ExecutionResult]:
        """
        Append a code cell and execute it with real-time output streaming.

        This method:
        1. Appends a new code cell to the notebook
        2. Executes the code in the kernel
        3. Streams outputs to the cell in real-time as they arrive
        4. Performs a final write to ensure nbformat compliance

        Args:
            code: Source code for the new cell
            metadata: Optional metadata to attach to the cell
            timeout: Optional execution timeout in seconds

        Returns:
            Tuple of (cell_index, ExecutionResult)

        Example:
            ```python
            idx, result = await session.append_and_run("print('Hello')")
            if result.status == "ok":
                print(f"Cell {idx} executed successfully")
            ```
        """
        logger.debug(f"[append_and_run] Appending code cell: {code!r}")

        # Use same smart startup logic as execute_notebook_cell to avoid race conditions
        if not self._started:
            kernel_alive = await self.kernel.is_alive()
            doc_connected = await self.doc.is_connected()

            if kernel_alive and doc_connected:
                # Components already started individually, just mark session as started
                logger.debug("Components already started individually, marking session as started")
                self._started = True
            else:
                # Normal startup flow
                logger.debug("Starting session with normal startup flow")
                await self.start()

        # Create the cell first so we have a stable index to update
        idx = await self.doc.append_code_cell(code, metadata=metadata)
        logger.debug(f"[append_and_run] Appended cell at index: {idx}")

        # Execute with streaming using the centralized helper
        result = await self._execute_with_streaming(code, idx, timeout)

        return idx, result

    async def run_at(
        self,
        index: int,
        code: str,
        *,
        timeout: float | None = None,
    ) -> ExecutionResult:
        """
        Replace the source of an existing cell and execute it with real-time output streaming.

        This method:
        1. Updates the source code of the cell at the specified index
        2. Executes the new code in the kernel
        3. Streams outputs to the cell in real-time as they arrive
        4. Performs a final write to ensure nbformat compliance

        Args:
            index: Zero-based index of the cell to update
            code: New source code for the cell
            timeout: Optional execution timeout in seconds

        Returns:
            ExecutionResult: Complete execution result

        Raises:
            IndexError: If the cell index is out of range

        Example:
            ```python
            result = await session.run_at(0, "print('Updated cell')")
            if result.status == "ok":
                print("Cell updated and executed successfully")
            ```
        """
        # Use same smart startup logic as execute_notebook_cell to avoid race conditions
        if not self._started:
            kernel_alive = await self.kernel.is_alive()
            doc_connected = await self.doc.is_connected()

            if kernel_alive and doc_connected:
                # Components already started individually, just mark session as started
                logger.debug("Components already started individually, marking session as started")
                self._started = True
            else:
                # Normal startup flow
                logger.debug("Starting session with normal startup flow")
                await self.start()

        # Guard: only allow overwriting code cells, not markdown cells
        cell = await self.doc.get_cell(index)
        cell_type = cell.get("cell_type", "code")
        if cell_type != "code":
            raise TypeError(
                f"Cell at index {index} is a '{cell_type}' cell, not a 'code' cell. "
                f"notebook_code_run_existing can only target code cells."
            )

        # Update the cell source first
        await self.doc.set_cell_source(index, code)

        # Execute with streaming using the centralized helper
        return await self._execute_with_streaming(code, index, timeout)

    async def run_markdown(self, text: str, *, index: int | None = None) -> int:
        """
        Add a markdown cell to the notebook.

        Args:
            text: Markdown content for the cell
            index: Optional position to insert the cell. If None, appends to end.

        Returns:
            int: Zero-based index of the cell

        Example:
            ```python
            idx = await session.run_markdown("# My Analysis")
            print(f"Added markdown cell at index {idx}")
            ```
        """
        # Use same smart startup logic as execute_notebook_cell to avoid race conditions
        if not self._started:
            kernel_alive = await self.kernel.is_alive()
            doc_connected = await self.doc.is_connected()

            if kernel_alive and doc_connected:
                # Components already started individually, just mark session as started
                logger.debug("Components already started individually, marking session as started")
                self._started = True
            else:
                # Normal startup flow
                logger.debug("Starting session with normal startup flow")
                await self.start()

        if index is None:
            result_index = await self.doc.append_markdown_cell(text)
        else:
            await self.doc.insert_markdown_cell(index, text)
            result_index = index

        return result_index
