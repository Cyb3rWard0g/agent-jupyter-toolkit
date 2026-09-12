from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..kernel import ExecutionResult
from ..kernel import Session as KernelSession
from ..kernel.types import KernelDisconnectedError
from .transport import NotebookDocumentTransport
from .types import CellDeletedError, CellRunResult, CellSourceChangedError, RunAllResult
from .utils import to_nbformat_outputs

logger = logging.getLogger(__name__)


def _output_fingerprint(output: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(output, sort_keys=True).encode("utf-8")).hexdigest()


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
    _display_targets: dict[str, dict[str, tuple[str, dict[int, str]]]] = field(
        default_factory=dict, init=False, repr=False
    )
    _display_generation: int | None = field(default=None, init=False, repr=False)

    # --------------------------------------------------------------------- lifecycle

    async def __aenter__(self) -> NotebookSession:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def start(self) -> None:
        """
        Start kernel then document transport (idempotent).
        If either startup step fails, partial document and kernel resources are closed.
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
        except BaseException:
            self._started = False
            with contextlib.suppress(Exception):
                await self.doc.stop()
            with contextlib.suppress(Exception):
                await self.kernel.shutdown()
            raise

    async def stop(self) -> None:
        """
        Stop document transport then kernel (idempotent, fault-tolerant).
        Always attempts to shut down the kernel, even if not fully started here.
        """
        self._display_targets.clear()
        self._display_generation = None
        if not self._started:
            with contextlib.suppress(Exception):
                await self.doc.stop()
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
        *,
        cell_id: str | None = None,
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
        if cell_id is None:
            cell = await self.doc.get_cell(cell_index)
            cell_id = cell.get("id")
        source_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        info = getattr(self.kernel, "session_info", None)
        generation = info().kernel_generation if callable(info) else 0
        if generation != self._display_generation:
            self._display_targets.clear()
            self._display_generation = generation
        # Executing this cell replaces its prior output area, even when its
        # source is unchanged. Old display handles must no longer target it.
        for display_id, targets in list(self._display_targets.items()):
            targets.pop(cell_id, None)
            if not targets:
                del self._display_targets[display_id]
        persistence_error: Exception | None = None

        async def _persist(outputs, execution_count) -> None:
            nonlocal persistence_error
            if isinstance(persistence_error, (CellDeletedError, CellSourceChangedError)):
                return
            try:
                updater = getattr(self.doc, "update_cell_outputs_by_id", None)
                if cell_id and callable(updater):
                    await updater(
                        cell_id,
                        list(outputs or []),
                        execution_count,
                        expected_source=code,
                    )
                else:
                    current = await self.doc.get_cell(cell_index)
                    if current.get("source") != code:
                        raise CellSourceChangedError(
                            f"Cell at index {cell_index} changed during execution"
                        )
                    await self.doc.update_cell_outputs(
                        cell_index,
                        list(outputs or []),
                        execution_count,
                    )
                persistence_error = None
            except Exception as exc:
                persistence_error = exc
                logger.debug("Failed to persist outputs for cell %s: %s", cell_id, exc)

        disconnected: KernelDisconnectedError | None = None
        try:
            res = await self.kernel.execute(
                code,
                timeout=timeout,
                output_callback=_persist,
                metadata={
                    "cellId": cell_id,
                    "agent_jupyter_toolkit": {"source_hash": source_hash},
                },
            )
        except KernelDisconnectedError as exc:
            disconnected = exc
            res = exc.partial_result or ExecutionResult()
            res.status = "error"
            res.outcome = "unknown"
            res.stderr += f"\n{exc}"
            exc.partial_result = res
        res.cell_id = cell_id
        res.source_hash = source_hash

        await _persist(to_nbformat_outputs(res), res.execution_count)
        cell_persisted = persistence_error is None
        cross_cell_error = await self._persist_cross_cell_display_updates(
            res, current_cell_id=cell_id
        )
        if cross_cell_error is not None and persistence_error is None:
            persistence_error = cross_cell_error
        if cell_id and cell_persisted:
            for display_id, indices in res.display_ids.items():
                self._display_targets.setdefault(display_id, {})[cell_id] = (
                    code,
                    {index: _output_fingerprint(res.outputs[index]) for index in indices},
                )
        if persistence_error is None:
            res.persistence_status = "ok"
        else:
            res.persistence_status = "error"
            res.persistence_error = f"{type(persistence_error).__name__}: {persistence_error}"
            logger.warning(
                "Execution finished but outputs were not persisted for cell %s: %s",
                cell_id,
                persistence_error,
            )
        if disconnected is not None:
            raise disconnected
        return res

    async def _persist_cross_cell_display_updates(
        self, result: ExecutionResult, *, current_cell_id: str | None
    ) -> Exception | None:
        persistence_error: Exception | None = None
        for display_id, update in result.display_updates.items():
            targets = self._display_targets.get(display_id, {})
            for target_cell_id, (expected_source, expected_outputs) in list(targets.items()):
                if target_cell_id == current_cell_id:
                    continue
                try:
                    cell = await self.doc.get_cell_by_id(target_cell_id)
                    outputs = list(cell.get("outputs") or [])
                    # External clears or reruns invalidate the output positions
                    # associated with this handle; never overwrite their replacements.
                    if cell.get("source") != expected_source or any(
                        index >= len(outputs) or _output_fingerprint(outputs[index]) != expected
                        for index, expected in expected_outputs.items()
                    ):
                        targets.pop(target_cell_id, None)
                        continue
                    for index in expected_outputs:
                        outputs[index] = {**outputs[index], **deepcopy(update)}
                    await self.doc.update_cell_outputs_by_id(
                        target_cell_id,
                        outputs,
                        cell.get("execution_count"),
                        expected_source=expected_source,
                    )
                    targets[target_cell_id] = (
                        expected_source,
                        {index: _output_fingerprint(outputs[index]) for index in expected_outputs},
                    )
                except CellDeletedError:
                    targets.pop(target_cell_id, None)
                except Exception as exc:
                    if persistence_error is None:
                        persistence_error = exc
                    logger.warning(
                        "Failed to persist cross-cell display update %s: %s", display_id, exc
                    )
        return persistence_error

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
        cell = await self.doc.get_cell(idx)
        cell_id = cell.get("id")
        logger.debug(f"[append_and_run] Appended cell at index: {idx}")

        # Execute with streaming using the centralized helper
        result = await self._execute_with_streaming(code, idx, timeout, cell_id=cell_id)
        if cell_id:
            with contextlib.suppress(KeyError):
                idx = await self.doc.resolve_cell_index(cell_id)

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
        updated_cell = await self.doc.get_cell(index)
        cell_id = updated_cell.get("id")

        # Execute with streaming using the centralized helper
        return await self._execute_with_streaming(
            code,
            index,
            timeout,
            cell_id=cell_id,
        )

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
                result = await self._execute_with_streaming(
                    source,
                    idx,
                    timeout,
                    cell_id=cell_id,
                )
                elapsed = _time.monotonic() - cell_start

                cr = CellRunResult(
                    index=idx,
                    cell_id=cell_id,
                    status=result.status,
                    source_snippet=preview,
                    execution_count=result.execution_count,
                    elapsed_seconds=elapsed,
                    persistence_status=result.persistence_status,
                    persistence_error=result.persistence_error,
                    request_id=result.request_id,
                    source_hash=result.source_hash,
                    kernel_generation=result.kernel_generation,
                    output_truncated=result.output_truncated,
                    dropped_output_bytes=result.dropped_output_bytes,
                    outcome=result.outcome,
                    timed_out=result.timed_out,
                )

                cell_failed = result.status != "ok" or result.persistence_status == "error"
                if cell_failed:
                    cr.error_message = (
                        result.persistence_error or result.stderr or "execution error"
                    )
                    overall = "error"
                    if failed is None:
                        failed = cr

                cell_results.append(cr)

                if cell_failed and stop_on_error:
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
                if isinstance(exc, KernelDisconnectedError):
                    cr.outcome = "unknown"
                    if exc.partial_result is not None:
                        for name in (
                            "persistence_status",
                            "persistence_error",
                            "request_id",
                            "source_hash",
                            "kernel_generation",
                            "output_truncated",
                            "dropped_output_bytes",
                            "execution_count",
                            "timed_out",
                        ):
                            setattr(cr, name, getattr(exc.partial_result, name))
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
