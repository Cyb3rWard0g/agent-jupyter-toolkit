from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..kernel import ExecutionResult
from ..kernel import Session as KernelSession
from .transport import NotebookDocumentTransport
from .types import CellRunResult, RunAllResult
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

        await self._ensure_started()

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
        await self._ensure_started()

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
        await self._ensure_started()

        if index is None:
            result_index = await self.doc.append_markdown_cell(text)
        else:
            await self.doc.insert_markdown_cell(index, text)
            result_index = index

        return result_index

    # --------------------------------------------------------------------- run-all

    async def run_all(
        self,
        *,
        stop_on_error: bool = True,
        timeout: float | None = None,
    ) -> RunAllResult:
        """
        Execute every code cell in the notebook sequentially.

        Iterates over all cells in document order, marks non-code and
        empty-source cells as skipped, and executes each remaining code cell
        via the kernel with real-time output streaming. Results are collected
        in notebook order so callers can inspect individual outcomes.

        This is useful as a **verification pass** — for example after a
        series of incremental edits an agent can ``run_all()`` to confirm
        the notebook still executes cleanly from top to bottom.

        Args:
            stop_on_error: If ``True`` (default), execution halts at the
                first cell that produces an error status.  If ``False``,
                all code cells are executed regardless of earlier failures.
            timeout: Per-cell execution timeout in seconds.  ``None`` means
                no timeout (the kernel decides when the cell is done).

        Returns:
            RunAllResult: Aggregate result with per-cell breakdown.

        Example:
            ```python
            result = await session.run_all()
            if result.status == "ok":
                print(f"All {result.executed_count} cells passed")
            else:
                print(f"Failed at cell {result.first_failure.index}")
            ```
        """
        import time as _time

        await self._ensure_started()

        total_start = _time.monotonic()
        cell_results: list[CellRunResult] = []
        executed = 0
        skipped = 0
        overall = "ok"
        failed: CellRunResult | None = None

        notebook = await self.doc.fetch()
        cells = notebook.get("cells") or []
        if not isinstance(cells, list):
            cells = []

        for idx, cell in enumerate(cells):
            cell_type = cell.get("cell_type", "")
            raw_source = cell.get("source", "")
            if isinstance(raw_source, list):
                source = "".join(str(part) for part in raw_source)
            else:
                source = str(raw_source)
            cell_id = cell.get("id")
            preview = source[:100]

            if cell_type != "code":
                skipped += 1
                cell_results.append(
                    CellRunResult(
                        index=idx,
                        cell_id=cell_id,
                        status="skipped",
                        source_snippet=preview,
                    )
                )
                continue

            if not source.strip():
                skipped += 1
                cell_results.append(
                    CellRunResult(
                        index=idx,
                        cell_id=cell_id,
                        status="skipped",
                        source_snippet=preview,
                    )
                )
                continue

            # Execute
            executed += 1
            cell_start = _time.monotonic()
            try:
                result = await self._execute_with_streaming(source, idx, timeout)
                elapsed = _time.monotonic() - cell_start

                cr = CellRunResult(
                    index=idx,
                    cell_id=cell_id,
                    status=result.status,
                    source_snippet=preview,
                    execution_count=result.execution_count,
                    elapsed_seconds=elapsed,
                )

                if result.status != "ok":
                    cr.error_message = result.stderr or "execution error"
                    overall = "error"
                    if failed is None:
                        failed = cr

                cell_results.append(cr)

                if result.status != "ok" and stop_on_error:
                    break

            except Exception as exc:
                elapsed = _time.monotonic() - cell_start
                cr = CellRunResult(
                    index=idx,
                    cell_id=cell_id,
                    status="error",
                    source_snippet=preview,
                    error_message=f"{type(exc).__name__}: {exc}",
                    elapsed_seconds=elapsed,
                )
                cell_results.append(cr)
                overall = "error"
                if failed is None:
                    failed = cr
                if stop_on_error:
                    break

        total_elapsed = _time.monotonic() - total_start

        return RunAllResult(
            status=overall,
            executed_count=executed,
            skipped_count=skipped,
            cells=cell_results,
            first_failure=failed,
            elapsed_seconds=total_elapsed,
        )

    async def restart_and_run_all(
        self,
        *,
        stop_on_error: bool = True,
        timeout: float | None = None,
    ) -> RunAllResult:
        """
        Restart the kernel and then execute every code cell sequentially.

        Combines :pymeth:`kernel.restart` with :pymeth:`run_all` into a
        single atomic verification workflow.  After the restart the kernel
        has a clean namespace, so this is the most rigorous way to confirm a
        notebook is reproducible from scratch.

        Args:
            stop_on_error: If ``True`` (default), execution halts at the
                first cell that produces an error status.
            timeout: Per-cell execution timeout in seconds.

        Returns:
            RunAllResult: Same structure as ``run_all()``.

        Example:
            ```python
            result = await session.restart_and_run_all()
            if result.status == "ok":
                print("Notebook is fully reproducible!")
            ```
        """
        await self._ensure_started()
        await self.kernel.restart()
        return await self.run_all(stop_on_error=stop_on_error, timeout=timeout)

    async def fresh_run_all(
        self,
        *,
        stop_on_error: bool = True,
        timeout: float | None = None,
    ) -> RunAllResult:
        """Backward-compatible alias for :pymeth:`restart_and_run_all`."""
        return await self.restart_and_run_all(
            stop_on_error=stop_on_error,
            timeout=timeout,
        )

    # --------------------------------------------------------------------- helpers

    async def _ensure_started(self) -> None:
        """Start the session if not already started (idempotent)."""
        if not self._started:
            kernel_alive = await self.kernel.is_alive()
            doc_connected = await self.doc.is_connected()

            if kernel_alive and doc_connected:
                logger.debug("Components already started individually, marking session as started")
                self._started = True
            else:
                logger.debug("Starting session with normal startup flow")
                await self.start()

    # ------------------------------------------------------------ dependency tracking

    #: Metadata key under which agent-installed dependencies are recorded.
    DEPS_META_KEY: str = "agent_dependencies"

    async def install_packages(
        self,
        packages: list[str],
        *,
        track: bool = True,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """
        Install packages into the kernel and optionally track them in notebook metadata.

        This is the **recommended** high-level API that combines kernel-level
        installation (via pip/uv) with notebook-level dependency tracking.
        After a successful install the package names and resolved versions
        are recorded under ``notebook.metadata.agent_dependencies`` so the
        notebook becomes self-documenting and reproducible.

        Args:
            packages: pip distribution names to install (e.g. ``["pandas", "plotly"]``).
            track: If True (default), record successfully installed packages in
                   notebook metadata.
            timeout: Per-operation timeout in seconds.

        Returns:
            The same ``{"success": bool, "report": {...}}`` dict returned by
            :func:`~agent_jupyter_toolkit.utils.packages.ensure_packages_with_report`,
            augmented with a ``"tracked"`` key listing what was written to metadata.
        """
        from ..utils.packages import ensure_packages_with_report

        await self._ensure_started()

        report = await ensure_packages_with_report(self.kernel, packages, timeout=timeout)

        tracked: list[str] = []
        if track and report.get("success"):
            succeeded = [
                pkg for pkg, info in (report.get("report") or {}).items() if info.get("success")
            ]
            if succeeded:
                try:
                    await self._track_dependencies(succeeded, timeout=timeout)
                    tracked = succeeded
                except Exception as exc:
                    logger.warning("Failed to track dependencies in metadata: %s", exc)

        report["tracked"] = tracked
        return report

    async def uninstall_packages(
        self,
        packages: list[str],
        *,
        untrack: bool = True,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """
        Uninstall packages from the kernel and remove them from notebook metadata.

        Args:
            packages: pip distribution names to uninstall.
            untrack: If True (default), remove uninstalled packages from
                     ``notebook.metadata.agent_dependencies``.
            timeout: Per-operation timeout in seconds.

        Returns:
            ``{"success": bool, "report": {...}}`` from the kernel uninstall,
            augmented with ``"untracked"`` listing packages removed from metadata.
        """
        from ..utils.packages import uninstall_packages as _uninstall_packages

        await self._ensure_started()

        report = await _uninstall_packages(self.kernel, packages, timeout=timeout)

        untracked: list[str] = []
        if untrack:
            removed = [
                pkg for pkg, info in (report.get("report") or {}).items() if info.get("uninstalled")
            ]
            if removed:
                try:
                    await self._untrack_dependencies(removed)
                    untracked = removed
                except Exception as exc:
                    logger.warning("Failed to untrack dependencies from metadata: %s", exc)

        report["untracked"] = untracked
        return report

    async def get_tracked_dependencies(self) -> dict[str, Any]:
        """
        Return the dependency manifest stored in notebook metadata.

        Returns:
            A dict mapping package name → ``{"version": str, "installed_at": str}``,
            or an empty dict if no dependencies have been tracked yet.
        """
        meta = await self.doc.get_metadata()
        return dict(meta.get(self.DEPS_META_KEY) or {})

    async def _track_dependencies(self, packages: list[str], *, timeout: float = 60.0) -> None:
        """
        Record *packages* (with resolved versions) in notebook metadata.

        This is an internal helper called after a successful install.
        """
        from ..utils.packages import get_package_versions

        versions = await get_package_versions(self.kernel, packages, timeout=timeout)
        now = datetime.now(UTC).isoformat()

        existing = await self.get_tracked_dependencies()
        for pkg in packages:
            existing[pkg] = {
                "version": versions.get(pkg),
                "installed_at": now,
            }

        await self.doc.update_metadata({self.DEPS_META_KEY: existing})
        logger.info(
            "Tracked %d dependencies in notebook metadata: %s",
            len(packages),
            packages,
        )

    async def _untrack_dependencies(self, packages: list[str]) -> None:
        """Remove *packages* from the dependency manifest in metadata."""
        existing = await self.get_tracked_dependencies()
        changed = False
        for pkg in packages:
            if pkg in existing:
                del existing[pkg]
                changed = True
        if changed:
            await self.doc.update_metadata({self.DEPS_META_KEY: existing})
            logger.info("Untracked packages from notebook metadata: %s", packages)
