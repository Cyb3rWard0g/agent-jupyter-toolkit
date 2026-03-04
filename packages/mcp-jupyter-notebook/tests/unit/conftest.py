from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_session():
    """Fixture for a mock NotebookSession with async methods."""
    session = MagicMock()
    session.is_connected = AsyncMock(return_value=True)
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.run_markdown = AsyncMock(return_value=42)
    session.kernel = MagicMock()
    session.kernel.is_alive = AsyncMock(return_value=True)
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
