"""MCP lifespan context and configuration adapter for the core notebook workspace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent_jupyter_toolkit.notebook import NotebookWorkspace, NotebookWorkspaceConfig

if TYPE_CHECKING:
    from agent_jupyter_toolkit.notebook import NotebookSession


class SessionManager(NotebookWorkspace):
    """Adapt MCP server configuration to the toolkit's notebook workspace.

    Retains the existing import and constructor for MCP integrations. Notebook
    lifecycle, registry, and default selection are implemented by the toolkit.
    """

    def __init__(self, config: dict[str, Any], default_path: str | None = None) -> None:
        super().__init__(
            NotebookWorkspaceConfig(
                mode=config["mode"],
                kernel_name=config.get("kernel_name", "python3"),
                base_url=config.get("base_url"),
                token=config.get("token"),
                headers=config.get("headers"),
                prefer_collab=config.get("prefer_collab", True),
                collaboration_mode=config.get("collaboration_mode"),
            ),
            default_path=default_path,
        )

    def get(self, path: str | None = None) -> NotebookSession:
        """Resolve a session and keep recovery instructions specific to MCP tools."""
        try:
            return super().get(path)
        except ValueError as exc:
            raise ValueError(f"{exc} Use notebook_open to open the notebook.") from exc


@dataclass
class AppContext:
    """Shared resources available to all MCP tool handlers.

    Attributes
    ----------
    manager : NotebookWorkspace
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

    manager: NotebookWorkspace

    @property
    def session(self) -> NotebookSession:
        """Return the default session (backward-compatible access)."""
        return self.manager.get()
