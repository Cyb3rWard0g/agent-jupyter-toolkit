from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_jupyter_toolkit.kernel.types import HistoryResult
from agent_jupyter_toolkit.notebook.types import RunAllResult


@pytest.fixture
def mock_session():
    """Fixture for a mock NotebookSession with async methods."""
    session = MagicMock()
    session.is_connected = AsyncMock(return_value=True)
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.run_markdown = AsyncMock(return_value=42)
    session.run_all = AsyncMock(return_value=RunAllResult(status="ok"))
    session.restart_and_run_all = AsyncMock(return_value=RunAllResult(status="ok"))
    session.get_cell = AsyncMock(
        return_value={
            "id": "cell-1",
            "cell_type": "code",
            "source": "print('hi')",
            "metadata": {},
            "outputs": [],
            "execution_count": 1,
        }
    )
    session.get_cell_source = AsyncMock(return_value="print('hi')")
    session.cell_count = AsyncMock(return_value=1)
    session.doc = MagicMock()
    session.doc.fetch = AsyncMock(return_value={"cells": [], "metadata": {}})
    session.doc.delete_cell = AsyncMock()
    session.doc.get_cell_by_id = AsyncMock(
        return_value={
            "id": "cell-1",
            "cell_type": "code",
            "source": "print('hi')",
            "metadata": {},
            "outputs": [],
            "execution_count": 1,
        }
    )
    session.doc.resolve_cell_index = AsyncMock(return_value=0)
    session.doc.set_cell_source = AsyncMock()
    session.doc.move_cell = AsyncMock()
    session.kernel = MagicMock()
    session.kernel.is_alive = AsyncMock(return_value=True)
    session.kernel.restart = AsyncMock()
    session.kernel.history = AsyncMock(return_value=HistoryResult(history=[]))
    session.install_packages = AsyncMock(
        return_value={"success": True, "report": {}, "tracked": []}
    )
    session.uninstall_packages = AsyncMock(
        return_value={"success": True, "report": {}, "untracked": []}
    )
    session.get_tracked_dependencies = AsyncMock(return_value={})
    return session


@pytest.fixture
def mock_manager(mock_session):
    """Fixture for a mock SessionManager that returns mock_session."""
    from mcp_jupyter_notebook.context import SessionManager

    manager = MagicMock(spec=SessionManager)
    manager.get.return_value = mock_session
    manager.default_path = "test.ipynb"
    manager.paths = ["test.ipynb"]
    manager.list_sessions.return_value = [{"notebook_path": "test.ipynb", "is_default": True}]
    manager.open = AsyncMock(return_value=mock_session)
    manager.close = AsyncMock(return_value=True)
    manager.close_all = AsyncMock()
    return manager


@pytest.fixture
def mock_ctx(mock_session, mock_manager):
    """Fixture for a mock MCP Context with lifespan_context."""
    from mcp_jupyter_notebook.context import AppContext

    app_context = AppContext(manager=mock_manager)

    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.lifespan_context = app_context
    ctx.info = AsyncMock()
    ctx.warning = AsyncMock()
    ctx.debug = AsyncMock()
    ctx.error = AsyncMock()
    return ctx
