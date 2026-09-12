"""
Session factory for agent-jupyter-toolkit.

Chooses between local (ZMQ) and server (HTTP+WS) transports and returns a
`Session` object with a small, stable async API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .transport import KernelTransport
from .transports.local import LocalTransport
from .transports.server import ServerTransport
from .types import (
    CompleteResult,
    ExecutionResult,
    HistoryResult,
    InspectResult,
    IsCompleteResult,
    KernelDisconnectedError,
    KernelInfoResult,
    OutputCallback,
    SessionConfig,
    SessionInfo,
)

if TYPE_CHECKING:
    from .manager import KernelManager


class Session:
    """
    A live kernel session wrapping a KernelTransport.

    Exposes a simple async API:
        - start()
        - execute(code, timeout=None) -> ExecutionResult
        - is_alive()
        - shutdown()

    Also supports use as an async context manager:

        async with create_session(cfg) as sess:
            await sess.execute("print('hi')")

    For advanced/local scenarios, `kernel_manager` is exposed when available.
    """

    def __init__(self, transport: KernelTransport) -> None:
        self._transport = transport
        self._kernel_generation = 0

    async def start(self) -> None:
        """
        Start or attach to the underlying kernel and initialize channels.
        """
        await self._transport.start()

    async def shutdown(self) -> None:
        """Release this client's resources and stop kernels it owns.

        An attached local kernel or existing remote notebook session is borrowed,
        so normal shutdown only detaches from it. Use :meth:`shutdown_kernel` when
        the caller explicitly intends to terminate a borrowed kernel.
        """
        await self._transport.shutdown()

    async def shutdown_kernel(self) -> None:
        """Explicitly terminate the kernel, including one attached from elsewhere."""
        await self._transport.shutdown_kernel()

    async def is_alive(self) -> bool:
        """Return True if the underlying kernel is responsive."""
        return await self._transport.is_alive()

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
        allow_stdin: bool = False,
        stop_on_error: bool = True,
    ) -> ExecutionResult:
        """
        Execute `code` in the underlying kernel.

        Args:
            code: Source code to run.
            timeout: Max seconds to wait for completion (None = no timeout).
            output_callback: If provided, this coroutine is called after each
                IOPub message that changes visible state, with
                `(outputs_so_far, execution_count)`. This allows real-time
                streaming of outputs into a collaborative document transport.
                Calls are strictly ordered as messages arrive; outputs is nbformat-like.
                May be invoked multiple times per cell.
            store_history: Whether to record execution in kernel history.
            allow_stdin: Whether the kernel may request stdin from this client.
            stop_on_error: Abort the queue if an error occurs in execution.

        Returns:
            ExecutionResult: Normalized execution metadata and nbformat-like outputs.
        """
        try:
            result = await self._transport.execute(
                code,
                timeout=timeout,
                output_callback=output_callback,
                silent=silent,
                store_history=store_history,
                user_expressions=user_expressions,
                metadata=metadata,
                allow_stdin=allow_stdin,
                stop_on_error=stop_on_error,
            )
        except KernelDisconnectedError as exc:
            if exc.partial_result is not None:
                exc.partial_result.kernel_generation = self._kernel_generation
            raise
        result.kernel_generation = self._kernel_generation
        return result

    # ── Introspection / control ──────────────────────────────────────────

    async def restart(self) -> None:
        """
        Restart the kernel, preserving the transport connection.

        After restart the kernel has a fresh namespace but the session
        remains usable — callers do **not** need to call ``start()`` again.

        For local kernels this restarts the OS process; for remote kernels
        this issues a ``POST /api/kernels/{id}/restart`` request.

        Raises:
            RuntimeError: If the transport is not started or the restart fails.
        """
        await self._transport.restart()
        self._kernel_generation += 1

    async def interrupt(self) -> None:
        """
        Interrupt the currently running execution in the kernel.

        Sends a SIGINT (or platform-equivalent) to the kernel process.
        """
        await self._transport.interrupt()

    async def complete(self, code: str, cursor_pos: int) -> CompleteResult:
        """
        Request tab-completion suggestions from the kernel.

        Args:
            code: The code context for completion.
            cursor_pos: Unicode offset within *code* where the cursor is.

        Returns:
            CompleteResult with matches and cursor span.
        """
        return await self._transport.complete(code, cursor_pos)

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
        return await self._transport.inspect(code, cursor_pos, detail_level)

    async def is_complete(self, code: str) -> IsCompleteResult:
        """
        Ask the kernel whether *code* is syntactically complete.

        Args:
            code: Source fragment to check.

        Returns:
            IsCompleteResult with status and optional indentation hint.
        """
        return await self._transport.is_complete(code)

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
            n: Number of entries (for "tail") or max results.

        Returns:
            HistoryResult containing a list of HistoryEntry items.
        """
        return await self._transport.history(
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

    async def kernel_info(self) -> KernelInfoResult:
        """
        Retrieve metadata about the connected kernel.

        Returns:
            KernelInfoResult with protocol version, language info, implementation
            details, and banner text.
        """
        return await self._transport.kernel_info()

    def session_info(self) -> SessionInfo:
        """Return kernel identity and resource ownership without credentials."""
        manager = self.kernel_manager
        return SessionInfo(
            transport=type(self._transport).__name__,
            kernel_id=getattr(self._transport, "kernel_id", None)
            or (manager.kernel_id if manager else None),
            server_session_id=getattr(self._transport, "server_session_id", None),
            owns_kernel=bool(
                getattr(self._transport, "owns_kernel", False) or (manager and manager.owns_kernel)
            ),
            owns_session=bool(getattr(self._transport, "owns_session", False)),
            connection_file=manager.connection_file_path if manager else None,
            kernel_generation=self._kernel_generation,
            transport_encryption=manager.transport_encryption if manager else "disabled",
            encryption_enabled=bool(manager and manager.encryption_enabled),
        )

    @property
    def kernel_manager(self) -> KernelManager | None:
        """
        Expose the KernelManager if this is a local session; otherwise None.

        Useful for advanced callers who need manager-specific operations locally
        (e.g., inspecting the connection file). Server sessions return None.
        """
        return getattr(self._transport, "kernel_manager", None)

    # Async context manager sugar
    async def __aenter__(self) -> Session:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.shutdown()


def create_session(config: SessionConfig | None = None) -> Session:
    """
    Create a Session using either LocalTransport or ServerTransport.

    Returns:
        Session: a wrapper around the chosen KernelTransport.

    Raises:
        ValueError: if config.mode == "server" but no server config provided.
    """
    if config is None:
        config = SessionConfig()
    if config.mode == "server":
        if not config.server:
            raise ValueError("Server mode requires a ServerConfig")
        transport = ServerTransport(config.server)
    else:
        if config.connection_file_name and (
            config.cwd is not None
            or config.env is not None
            or config.kernel_args
            or config.transport_encryption != "disabled"
        ):
            raise ValueError(
                "cwd, env, kernel_args, and transport_encryption only apply when "
                "launching a local kernel"
            )
        transport = LocalTransport(
            kernel_name=config.kernel_name,
            connection_file_name=config.connection_file_name,
            packer=config.packer,
            startup_timeout=config.startup_timeout,
            cwd=config.cwd,
            env=config.env,
            kernel_args=config.kernel_args,
            max_output_bytes=config.max_output_bytes,
            transport_encryption=config.transport_encryption,
            manager_factory=config.manager_factory,
        )
    return Session(transport)
