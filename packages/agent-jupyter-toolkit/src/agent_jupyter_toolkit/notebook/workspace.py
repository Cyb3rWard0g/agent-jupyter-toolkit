"""Manage multiple notebook sessions independently of any agent or MCP framework."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ._workspace_files import create_workspace_file_backend

if TYPE_CHECKING:
    from .session import NotebookSession

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NotebookWorkspaceConfig:
    """Backend settings shared by the notebook sessions in one workspace.

    ``server`` connects to Jupyter's APIs; ``local`` starts local kernels.
    These modes are independent of how an application exposes its own tools.
    """

    mode: Literal["local", "server"] = "local"
    kernel_name: str = "python3"
    base_url: str | None = None
    token: str | None = field(default=None, repr=False)
    headers: dict[str, str] | None = field(default=None, repr=False)
    prefer_collab: bool = True
    collaboration_mode: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"local", "server"}:
            raise ValueError("Workspace mode must be 'local' or 'server'")


class NotebookWorkspace:
    """Map notebook paths to live :class:`NotebookSession` instances.

    Parameters
    ----------
    config : NotebookWorkspaceConfig | None
        Notebook backend settings. Defaults to local Python kernels.
    default_path : str | None
        Optional initial default, opened on async context entry. Otherwise
        the first notebook opened becomes the default. Each workspace has
        one default shared by all of its callers.
    """

    def __init__(
        self, config: NotebookWorkspaceConfig | None = None, default_path: str | None = None
    ) -> None:
        config = config or NotebookWorkspaceConfig()
        self._config = replace(config, headers=dict(config.headers or {}))
        self._sessions: dict[str, NotebookSession] = {}
        self._opening: dict[str, asyncio.Task[NotebookSession]] = {}
        self._closing: dict[str, asyncio.Task[None]] = {}
        self._deleting: dict[str, asyncio.Task[bool]] = {}
        self._shutdown_task: asyncio.Task[None] | None = None
        self._registry_lock = asyncio.Lock()
        self._shutting_down = False
        self._default_path: str | None = None
        self.default_path = default_path
        self._file_backend = create_workspace_file_backend(
            self._config.mode,
            base_url=self._config.base_url,
            token=self._config.token,
            headers=self._config.headers,
        )

    # ── public API ──────────────────────────────────

    async def __aenter__(self) -> NotebookWorkspace:
        if self._shutting_down:
            raise RuntimeError("Notebook workspace is shutting down")
        try:
            if self.default_path is not None:
                await self.open(self.default_path)
        except BaseException:
            # open() shields shared startup tasks from caller cancellation.
            # A failed context entry has no __aexit__, so drain them here.
            await self.close_all()
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close_all()

    @property
    def default_path(self) -> str | None:
        """Canonical default path, or None when no default is selected."""
        return self._default_path

    @default_path.setter
    def default_path(self, path: str | None) -> None:
        # Preserve assignment compatibility, including preselecting a path
        # before startup. Prefer set_default() when selecting an open session.
        self._default_path = self._session_key(path) if path is not None else None

    def set_default(self, path: str) -> None:
        """Select an already open notebook without stopping any other session."""
        if self._shutting_down:
            raise RuntimeError("Notebook workspace is shutting down")
        self.get(path)
        self.default_path = path

    def is_default(self, path: str) -> bool:
        """Compare a notebook path to the default using the registry's normalization."""
        return self._session_key(path) == self.default_path

    @property
    def paths(self) -> list[str]:
        """Return the paths of all currently open notebooks."""
        return list(self._sessions.keys())

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, path: str) -> bool:
        return self._session_key(path) in self._sessions

    async def open(self, path: str) -> NotebookSession:
        """Open a notebook, creating a new session if needed.

        If the notebook is already open the existing session is returned.
        Otherwise a fresh :class:`NotebookSession` is built from the
        workspace config, started, and registered in the workspace.

        Parameters
        ----------
        path : str
            Notebook file path (e.g. ``"analysis.ipynb"``).

        Returns
        -------
        NotebookSession

        Raises
        ------
        RuntimeError
            If the session cannot be created or started.
        """
        path = self._session_key(path)
        while True:
            async with self._registry_lock:
                if self._shutting_down:
                    raise RuntimeError(
                        "Session manager is shutting down; new sessions cannot be opened"
                    )
                teardown = self._deleting.get(path) or self._closing.get(path)
                if teardown is None:
                    existing = self._sessions.get(path)
                    if existing is not None:
                        log.debug("Reusing existing session for %s", path)
                        return existing
                    task = self._opening.get(path)
                    if task is None:
                        task = asyncio.create_task(
                            self._open_new_session(path),
                            name=f"jupyter-workspace-open:{path}",
                        )
                        self._opening[path] = task
                    break
            # A session removed from the registry may still be shutting down
            # its server kernel. Reopen only after that teardown completes.
            await asyncio.shield(teardown)
        return await asyncio.shield(task)

    async def close(self, path: str) -> bool:
        """Close a notebook session and release its resources.

        Parameters
        ----------
        path : str
            Notebook file path.

        Returns
        -------
        bool
            ``True`` if the session existed and was closed, ``False`` if
            it was not open.
        """
        path = self._session_key(path)
        while True:
            async with self._registry_lock:
                closing = self._closing.get(path)
                if closing is not None:
                    task = closing
                    break
                opening = self._opening.get(path)
                if opening is None:
                    session = self._sessions.pop(path, None)
                    if session is None:
                        return False
                    # Retarget as soon as the session leaves the registry.
                    # Slow transport teardown must not leave a stale default
                    # or overwrite a default selected by another caller later.
                    if self.default_path == path:
                        self.default_path = next(iter(self._sessions), None)
                    task = asyncio.create_task(
                        self._close_session(path, session),
                        name=f"jupyter-workspace-close:{path}",
                    )
                    self._closing[path] = task
                    break
            try:
                await asyncio.shield(opening)
            except Exception:
                pass

        await asyncio.shield(task)
        return True

    async def delete(self, path: str) -> bool:
        """Close a notebook session (if open) and delete the file.

        In **server** mode the file is removed via the Jupyter Contents
        API.  In **local** mode it is removed from the filesystem.

        Parameters
        ----------
        path : str
            Notebook file path.

        Returns
        -------
        bool
            ``True`` if the file was deleted (or already absent).

        Raises
        ------
        RuntimeError
            If the file exists but deletion fails.
        """
        path = self._session_key(path)
        async with self._registry_lock:
            if self._shutting_down:
                raise RuntimeError(
                    "Notebook workspace is shutting down; notebooks cannot be deleted"
                )
            task = self._deleting.get(path)
            if task is None:
                task = asyncio.create_task(
                    self._delete_path(path),
                    name=f"jupyter-workspace-delete:{path}",
                )
                self._deleting[path] = task
        return await asyncio.shield(task)

    async def close_all(self) -> None:
        """Close every session completely, even if the waiting caller is cancelled."""
        async with self._registry_lock:
            self._shutting_down = True
            if self._shutdown_task is None:
                paths = list(
                    dict.fromkeys(
                        [*self._sessions, *self._opening, *self._closing, *self._deleting]
                    )
                )
                deleting = list(dict.fromkeys(self._deleting.values()))
                self._shutdown_task = asyncio.create_task(
                    self._drain_shutdown(paths, deleting),
                    name="jupyter-workspace-shutdown",
                )
            task = self._shutdown_task
        await self._wait_for_shutdown(task)

    async def _drain_shutdown(
        self,
        paths: list[str],
        deleting: list[asyncio.Task[bool]],
    ) -> None:
        """Finish the terminal shutdown task without leaving later paths open."""
        errors: list[BaseException] = []
        for path in paths:
            try:
                await self.close(path)
            except BaseException as exc:
                errors.append(exc)
                log.exception("Failed to close notebook during workspace shutdown: %s", path)

        async with self._registry_lock:
            deleting = list(dict.fromkeys([*deleting, *self._deleting.values()]))
        if deleting:
            results = await asyncio.gather(
                *(asyncio.shield(task) for task in deleting),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    errors.append(result)
                    log.error("Notebook deletion failed during workspace shutdown: %s", result)

        if errors:
            raise errors[0]

    @staticmethod
    async def _wait_for_shutdown(task: asyncio.Task[None]) -> None:
        """Delay caller cancellation until the retained shutdown task has drained."""
        current = asyncio.current_task()
        cancellation_requests = 0
        shutdown_error: BaseException | None = None

        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if current is None or current.cancelling() == 0:
                    raise
                while current.cancelling():
                    current.uncancel()
                    cancellation_requests += 1
                if task.cancelled():
                    shutdown_error = exc
                    break
            except BaseException as exc:
                shutdown_error = exc
                break

        if shutdown_error is None and task.done():
            try:
                task.result()
            except BaseException as exc:
                shutdown_error = exc

        if cancellation_requests:
            if shutdown_error is not None:
                log.error(
                    "Workspace shutdown failed while its caller was cancelled",
                    exc_info=(
                        type(shutdown_error),
                        shutdown_error,
                        shutdown_error.__traceback__,
                    ),
                )
            assert current is not None
            for _ in range(cancellation_requests):
                current.cancel()
            raise asyncio.CancelledError() from shutdown_error

        if shutdown_error is not None:
            raise shutdown_error

    def get(self, path: str | None = None) -> NotebookSession:
        """Retrieve a session by path, falling back to the default.

        Parameters
        ----------
        path : str | None
            Notebook path.  When ``None`` the default notebook is used.

        Returns
        -------
        NotebookSession

        Raises
        ------
        ValueError
            If no path is given and no default is set, or if the
            requested notebook is not open.
        """
        resolved = self._session_key(path) if path is not None else self.default_path
        if resolved is None:
            raise ValueError(
                "No notebook_path specified and no default notebook is open. "
                "Call open(path) to open a notebook first."
            )
        session = self._sessions.get(resolved)
        if session is None:
            raise ValueError(
                f"Notebook '{resolved}' is not open. "
                f"Open notebooks: {self.paths or '(none)'}. "
                "Call open(path) to open it first."
            )
        return session

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return summary info for all open notebooks."""
        return [
            {
                "notebook_path": path,
                "is_default": path == self.default_path,
                **self.document_transport_info(session),
            }
            for path, session in self._sessions.items()
        ]

    async def list_notebook_files(
        self,
        directory: str = ".",
        recursive: bool = False,
    ) -> list[dict[str, Any]]:
        """List ``.ipynb`` files available for opening.

        In **local** mode this scans the filesystem.  In **server** mode
        it queries the Jupyter Contents API.

        Parameters
        ----------
        directory : str
            Root directory to search (default: current directory).
        recursive : bool
            If ``True``, search sub-directories as well.

        Returns
        -------
        list[dict[str, Any]]
            One entry per notebook with ``path``, ``name``, and
            ``is_open`` flag.
        """
        return await self._file_backend.list_notebooks(directory, recursive, self.__contains__)

    # ── private ─────────────────────────────────────

    def _session_key(self, path: str) -> str:
        """Return the canonical registry key used for a notebook path."""
        if self._config.mode == "local":
            return str(Path(path).expanduser().resolve())
        return path.strip("/")

    async def _open_new_session(self, path: str) -> NotebookSession:
        """Build, start, and register one shared in-flight session."""
        session: NotebookSession | None = None
        try:
            await self._file_backend.ensure_notebook(path)
            log.info("Opening notebook session: %s", path)
            session = self._build_session(path)
            await session.start()
            async with self._registry_lock:
                self._sessions[path] = session
                if self.default_path is None:
                    self.default_path = path
                    log.info("Default notebook set to: %s", path)
            return session
        except BaseException:
            if session is not None:
                try:
                    await session.stop()
                except Exception:
                    pass
            raise
        finally:
            async with self._registry_lock:
                if self._opening.get(path) is asyncio.current_task():
                    self._opening.pop(path, None)

    async def _close_session(self, path: str, session: NotebookSession) -> None:
        """Stop one session and release its per-path close barrier."""
        log.info("Closing notebook session: %s", path)
        try:
            await session.stop()
        except Exception as exc:
            log.warning("Error stopping session for %s: %s", path, exc)
        finally:
            async with self._registry_lock:
                if self._closing.get(path) is asyncio.current_task():
                    self._closing.pop(path, None)

    async def _delete_path(self, path: str) -> bool:
        """Close and delete a path while preventing it from being reopened."""
        try:
            await self.close(path)
            return await self._file_backend.delete_notebook(path)
        finally:
            async with self._registry_lock:
                if self._deleting.get(path) is asyncio.current_task():
                    self._deleting.pop(path, None)

    def _build_session(self, notebook_path: str) -> NotebookSession:
        """Build an uninitialised session for the given notebook path."""
        from agent_jupyter_toolkit.notebook import NotebookSession
        from agent_jupyter_toolkit.utils import create_kernel, create_notebook_transport

        cfg = self._config
        mode = cfg.mode
        kernel_name = cfg.kernel_name
        headers = cfg.headers or None

        if mode == "server":
            base_url = cfg.base_url
            if not base_url:
                raise RuntimeError("base_url is required in server mode")
            token = cfg.token

            kernel = create_kernel(
                "remote",
                base_url=base_url,
                token=token,
                headers=headers,
                kernel_name=kernel_name,
                notebook_path=notebook_path,
            )
            doc = create_notebook_transport(
                "remote",
                notebook_path,
                base_url=base_url,
                token=token,
                headers=headers,
                prefer_collab=cfg.prefer_collab,
                collaboration_mode=cfg.collaboration_mode,
                create_if_missing=True,
            )
            return NotebookSession(kernel=kernel, doc=doc)

        # local mode
        kernel = create_kernel("local", kernel_name=kernel_name)
        doc = create_notebook_transport(
            "local",
            notebook_path,
            prefer_collab=False,
            create_if_missing=True,
        )
        return NotebookSession(kernel=kernel, doc=doc)

    @staticmethod
    def document_transport_info(session: NotebookSession) -> dict[str, Any]:
        """Return the selected document transport without exposing credentials."""
        doc = session.doc
        selected = getattr(doc, "selected_transport", None)
        if selected is None:
            type_name = type(doc).__name__
            if type_name == "CollabYjsDocumentTransport":
                selected = "collaboration"
            elif type_name == "ContentsApiDocumentTransport":
                selected = "contents"
            elif type_name == "LocalFileDocumentTransport":
                selected = "local-file"
            else:
                selected = type_name
        return {
            "document_transport": selected,
            "collaboration_mode": getattr(doc, "collaboration_mode", "disabled"),
            "collaboration_fallback_reason": getattr(doc, "fallback_reason", None),
        }
