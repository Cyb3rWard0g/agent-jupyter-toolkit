"""Notebook file operations used by :mod:`agent_jupyter_toolkit.notebook.workspace`."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import aiohttp
import nbformat

log = logging.getLogger(__name__)

NotebookInfo = dict[str, Any]
IsOpen = Callable[[str], bool]


class WorkspaceFileBackend(Protocol):
    """Storage operations needed by a notebook workspace."""

    async def ensure_notebook(self, path: str) -> None: ...

    async def list_notebooks(
        self, directory: str, recursive: bool, is_open: IsOpen
    ) -> list[NotebookInfo]: ...

    async def delete_notebook(self, path: str) -> bool: ...


class LocalWorkspaceFiles:
    """Discover, create, and delete notebooks on the local filesystem."""

    async def ensure_notebook(self, path: str) -> None:
        notebook_path = Path(path)
        if notebook_path.exists():
            return
        log.info("Creating new notebook: %s", notebook_path)
        notebook_path.parent.mkdir(parents=True, exist_ok=True)
        nbformat.write(nbformat.v4.new_notebook(), str(notebook_path))

    async def list_notebooks(
        self, directory: str, recursive: bool, is_open: IsOpen
    ) -> list[NotebookInfo]:
        root = Path(directory).resolve()
        if not root.is_dir():
            return []

        pattern = "**/*.ipynb" if recursive else "*.ipynb"
        results: list[NotebookInfo] = []
        for path in sorted(root.glob(pattern)):
            relative = (
                str(path.relative_to(Path.cwd())) if path.is_relative_to(Path.cwd()) else str(path)
            )
            results.append(
                {
                    "path": relative,
                    "name": path.name,
                    "is_open": is_open(str(path)),
                }
            )
        return results

    async def delete_notebook(self, path: str) -> bool:
        notebook_path = Path(path).resolve()
        if not notebook_path.exists():
            log.debug("Local notebook already absent: %s", notebook_path)
            return True
        try:
            notebook_path.unlink()
        except OSError as exc:
            raise RuntimeError(f"Failed to delete {notebook_path}: {exc}") from exc
        log.info("Deleted local notebook: %s", notebook_path)
        return True


class ServerWorkspaceFiles:
    """Discover and delete notebooks through the Jupyter Contents API."""

    def __init__(
        self,
        base_url: str | None,
        *,
        token: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None
        self._headers = dict(headers or {})
        if token:
            self._headers["Authorization"] = f"Token {token}"

    async def ensure_notebook(self, path: str) -> None:
        # The document transport owns remote creation and its startup checks.
        return None

    async def list_notebooks(
        self, directory: str, recursive: bool, is_open: IsOpen
    ) -> list[NotebookInfo]:
        path = "" if directory == "." else directory
        results: list[NotebookInfo] = []
        async with aiohttp.ClientSession(headers=self._headers) as session:
            await self._fetch_contents(session, path, results, recursive, is_open)
        return results

    async def delete_notebook(self, path: str) -> bool:
        url = self._contents_url(path)
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.delete(url) as response:
                if response.status == 204:
                    log.info("Deleted server notebook: %s", path)
                    return True
                if response.status == 404:
                    log.debug("Server notebook already absent: %s", path)
                    return True
                await self._raise_response_error("DELETE", url, response)
        raise AssertionError("unreachable")

    async def _fetch_contents(
        self,
        session: aiohttp.ClientSession,
        path: str,
        results: list[NotebookInfo],
        recursive: bool,
        is_open: IsOpen,
    ) -> None:
        url = self._contents_url(path)
        async with session.get(url, params={"content": "1"}) as response:
            if response.status != 200:
                await self._raise_response_error("GET", url, response)
            data = await response.json()

        if data.get("type") != "directory":
            return
        for item in data.get("content", []):
            if item.get("type") == "notebook":
                notebook_path = str(item["path"])
                results.append(
                    {
                        "path": notebook_path,
                        "name": item["name"],
                        "is_open": is_open(notebook_path),
                    }
                )
            elif item.get("type") == "directory" and recursive:
                await self._fetch_contents(
                    session,
                    str(item["path"]),
                    results,
                    recursive,
                    is_open,
                )

    def _contents_url(self, path: str) -> str:
        """Build a Contents URL while retaining path separators and escaping data."""
        if self._base_url is None:
            raise RuntimeError("base_url is required in server mode")
        encoded_path = quote(path.strip("/"), safe="/")
        return f"{self._base_url}/api/contents/{encoded_path}"

    @staticmethod
    async def _raise_response_error(
        method: str, url: str, response: aiohttp.ClientResponse
    ) -> None:
        text = await response.text()
        raise RuntimeError(f"{method} {url} failed ({response.status}): {text}")


def create_workspace_file_backend(
    mode: str,
    *,
    base_url: str | None,
    token: str | None,
    headers: dict[str, str] | None,
) -> WorkspaceFileBackend:
    """Create the file backend corresponding to workspace mode."""
    if mode == "local":
        return LocalWorkspaceFiles()
    return ServerWorkspaceFiles(base_url, token=token, headers=headers)
