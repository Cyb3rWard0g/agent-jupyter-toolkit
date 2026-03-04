"""Shared lifespan context for the MCP Jupyter server.

:class:`AppContext` is yielded by the server lifespan
(:func:`~mcp_jupyter_notebook.server.app_lifespan`) and made available to
every tool handler through::

    ctx.request_context.lifespan_context.manager

The :class:`SessionManager` provides multi-notebook support, mapping
notebook paths to :class:`~agent_jupyter_toolkit.notebook.NotebookSession`
instances.  A ``default_path`` preserves backward compatibility — tools
that omit ``notebook_path`` automatically target the default notebook.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_jupyter_toolkit.notebook import NotebookSession

log = logging.getLogger("mcp-jupyter.context")


class SessionManager:
    """Map notebook paths to live :class:`NotebookSession` instances.

    Parameters
    ----------
    config : dict[str, Any]
        Server configuration produced by
        :func:`~mcp_jupyter_notebook.server.process_config`.
        Used to create new sessions on-the-fly when ``open`` is called.
    default_path : str | None
        The notebook that is opened automatically at startup.  Tools that
        omit ``notebook_path`` target this notebook.  May be ``None`` if
        no default is desired (agents must explicitly ``notebook_open``).
    """

    def __init__(self, config: dict[str, Any], default_path: str | None = None) -> None:
        self._config = config
        self._sessions: dict[str, NotebookSession] = {}
        self.default_path: str | None = default_path

    # ── public API ──────────────────────────────────

    @property
    def paths(self) -> list[str]:
        """Return the paths of all currently open notebooks."""
        return list(self._sessions.keys())

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, path: str) -> bool:
        return path in self._sessions

    async def open(self, path: str) -> NotebookSession:
        """Open a notebook, creating a new session if needed.

        If the notebook is already open the existing session is returned.
        Otherwise a fresh :class:`NotebookSession` is built from the
        server config, started, and registered in the manager.

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
        if path in self._sessions:
            log.debug("Reusing existing session for %s", path)
            return self._sessions[path]

        # In local mode, resolve the path and create the file if it doesn't exist.
        if self._config["mode"] == "local":
            nb_path = Path(path).resolve()
            if not nb_path.exists():
                log.info("Creating new notebook: %s", nb_path)
                nb_path.parent.mkdir(parents=True, exist_ok=True)
                import nbformat

                nb = nbformat.v4.new_notebook()
                nbformat.write(nb, str(nb_path))
            # Normalise to the resolved path for consistent keying
            path = str(nb_path)

        log.info("Opening notebook session: %s", path)
        session = self._build_session(path)
        await session.start()
        self._sessions[path] = session

        # If no default, make the first opened notebook the default
        if self.default_path is None:
            self.default_path = path
            log.info("Default notebook set to: %s", path)

        return session

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
        session = self._sessions.pop(path, None)
        if session is None:
            return False

        log.info("Closing notebook session: %s", path)
        try:
            await session.stop()
        except Exception as exc:
            log.warning("Error stopping session for %s: %s", path, exc)

        # Update default if we just closed it
        if self.default_path == path:
            self.default_path = next(iter(self._sessions), None)
            if self.default_path:
                log.info("Default notebook changed to: %s", self.default_path)
            else:
                log.info("No notebooks remaining; default cleared")

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
        # Close session first (idempotent — returns False if not open)
        await self.close(path)

        if self._config["mode"] == "local":
            return self._delete_local_file(path)
        else:
            return await self._delete_server_file(path)

    async def close_all(self) -> None:
        """Close every open session.  Used during server shutdown."""
        paths = list(self._sessions.keys())
        for p in paths:
            await self.close(p)

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
        resolved = path or self.default_path
        if resolved is None:
            raise ValueError(
                "No notebook_path specified and no default notebook is open. "
                "Use notebook_open to open a notebook first."
            )
        session = self._sessions.get(resolved)
        if session is None:
            raise ValueError(
                f"Notebook '{resolved}' is not open. "
                f"Open notebooks: {self.paths or '(none)'}. "
                "Use notebook_open to open it first."
            )
        return session

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return summary info for all open notebooks."""
        return [
            {
                "notebook_path": path,
                "is_default": path == self.default_path,
            }
            for path in self._sessions
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
        if self._config["mode"] == "local":
            return self._list_local_files(directory, recursive)
        else:
            return await self._list_server_files(directory, recursive)

    # ── private ─────────────────────────────────────

    def _list_local_files(self, directory: str, recursive: bool) -> list[dict[str, Any]]:
        """Scan the local filesystem for ``.ipynb`` files."""
        root = Path(directory).resolve()
        if not root.is_dir():
            return []

        pattern = "**/*.ipynb" if recursive else "*.ipynb"
        results = []
        for p in sorted(root.glob(pattern)):
            rel = str(p.relative_to(Path.cwd())) if p.is_relative_to(Path.cwd()) else str(p)
            results.append(
                {
                    "path": rel,
                    "name": p.name,
                    "is_open": str(p.resolve()) in self._sessions or rel in self._sessions,
                }
            )
        return results

    async def _list_server_files(self, directory: str, recursive: bool) -> list[dict[str, Any]]:
        """List notebooks from a Jupyter server via the Contents API."""
        import aiohttp

        cfg = self._config
        base_url = cfg["base_url"].rstrip("/")
        headers: dict[str, str] = dict(cfg.get("headers") or {})
        if cfg.get("token"):
            headers["Authorization"] = f"Token {cfg['token']}"

        # Normalise directory path for the Contents API
        api_path = directory.strip("/") if directory != "." else ""
        url = f"{base_url}/api/contents/{api_path}"

        results: list[dict[str, Any]] = []
        async with aiohttp.ClientSession(headers=headers) as session:
            await self._fetch_contents(session, url, results, recursive)
        return results

    async def _fetch_contents(
        self,
        session: Any,
        url: str,
        results: list[dict[str, Any]],
        recursive: bool,
    ) -> None:
        """Recursively fetch notebook entries from the Contents API."""
        async with session.get(url, params={"content": "1"}) as resp:
            if resp.status != 200:
                log.warning("Contents API returned %s for %s", resp.status, url)
                return
            data = await resp.json()

        if data.get("type") == "directory":
            for item in data.get("content", []):
                if item["type"] == "notebook":
                    nb_path = item["path"]
                    results.append(
                        {
                            "path": nb_path,
                            "name": item["name"],
                            "is_open": nb_path in self._sessions,
                        }
                    )
                elif item["type"] == "directory" and recursive:
                    sub_url = url.rsplit("/api/contents/", 1)[0]
                    sub_url = f"{sub_url}/api/contents/{item['path']}"
                    await self._fetch_contents(session, sub_url, results, recursive)

    def _delete_local_file(self, path: str) -> bool:
        """Remove a notebook file from the local filesystem."""
        nb_path = Path(path).resolve()
        if not nb_path.exists():
            log.debug("Local notebook already absent: %s", nb_path)
            return True
        try:
            nb_path.unlink()
            log.info("Deleted local notebook: %s", nb_path)
            return True
        except Exception as exc:
            raise RuntimeError(f"Failed to delete {nb_path}: {exc}") from exc

    async def _delete_server_file(self, path: str) -> bool:
        """Remove a notebook file via the Jupyter Contents API."""
        from urllib.parse import quote

        import aiohttp

        cfg = self._config
        base_url = cfg["base_url"].rstrip("/")
        headers: dict[str, str] = dict(cfg.get("headers") or {})
        if cfg.get("token"):
            headers["Authorization"] = f"Token {cfg['token']}"

        url = f"{base_url}/api/contents/{quote(path, safe='')}"
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.delete(url) as resp:
                if resp.status == 204:
                    log.info("Deleted server notebook: %s", path)
                    return True
                if resp.status == 404:
                    log.debug("Server notebook already absent: %s", path)
                    return True
                text = await resp.text()
                raise RuntimeError(
                    f"Failed to delete notebook {path} " f"(HTTP {resp.status}): {text}"
                )

    def _build_session(self, notebook_path: str) -> NotebookSession:
        """Build an uninitialised session for the given notebook path."""
        from agent_jupyter_toolkit.notebook import NotebookSession
        from agent_jupyter_toolkit.utils import create_kernel, create_notebook_transport

        cfg = self._config
        mode = cfg["mode"]
        kernel_name = cfg["kernel_name"]
        headers = cfg.get("headers") or None

        if mode == "server":
            base_url = cfg["base_url"]
            if not base_url:
                raise RuntimeError(
                    "MCP_JUPYTER_BASE_URL (or --base-url) is required in server mode"
                )
            token = cfg.get("token")

            kernel = create_kernel(
                "remote",
                base_url=base_url,
                token=token,
                headers=headers,
                kernel_name=kernel_name,
            )
            doc = create_notebook_transport(
                "remote",
                notebook_path,
                base_url=base_url,
                token=token,
                headers=headers,
                prefer_collab=cfg.get("prefer_collab", True),
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


@dataclass
class AppContext:
    """Shared resources available to all MCP tool handlers.

    Attributes
    ----------
    manager : SessionManager
        The session manager providing access to one or more notebook
        sessions.
    session : NotebookSession
        Convenience property for the default session.  Provided for
        backward compatibility with code that accessed
        ``lifespan_context.session``.

    .. deprecated::
        Direct access to ``session`` is discouraged for new code.
        Use ``manager.get(notebook_path)`` instead.
    """

    manager: SessionManager

    @property
    def session(self) -> NotebookSession:
        """Return the default session (backward-compatible access)."""
        return self.manager.get()
