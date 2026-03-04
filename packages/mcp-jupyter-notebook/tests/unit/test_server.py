"""
Unit tests for server.py utilities and entrypoints.
"""

from mcp.server.fastmcp import FastMCP

from mcp_jupyter_notebook.server import _parse_headers_env


def test_create_server_registers_postgresql_tools_when_enabled(monkeypatch):
    """Optional tools are only registered when enabled in config."""
    import mcp_jupyter_notebook.server as server

    server._server_config = {"enabled_tools": ["postgresql"]}
    mcp = server.create_server()

    assert isinstance(mcp, FastMCP)
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    assert "postgresql_connect" in tool_names
    assert "postgresql_test_connection" in tool_names
    assert "postgresql_close" in tool_names
    assert "postgresql_query_to_df" in tool_names
    assert "postgresql_schema_list_tables" in tool_names
    assert "postgresql_schema_list_columns" in tool_names
    assert "postgresql_schema_tree" in tool_names
    assert "postgresql_reset" in tool_names


def test_parse_headers_env_valid(monkeypatch):
    """Test _parse_headers_env parses valid JSON headers from env."""
    monkeypatch.setenv(
        "MCP_JUPYTER_HEADERS_JSON", '{"Authorization": "Bearer token", "X-Test": "yes"}'
    )
    headers = _parse_headers_env()
    assert headers == {"Authorization": "Bearer token", "X-Test": "yes"}


def test_parse_headers_env_invalid_json(monkeypatch):
    """Test _parse_headers_env handles invalid JSON gracefully."""
    monkeypatch.setenv("MCP_JUPYTER_HEADERS_JSON", "{bad json}")
    headers = _parse_headers_env()
    assert headers == {}


def test_parse_headers_env_not_dict(monkeypatch):
    """Test _parse_headers_env handles non-dict JSON gracefully."""
    monkeypatch.setenv("MCP_JUPYTER_HEADERS_JSON", "[1,2,3]")
    headers = _parse_headers_env()
    assert headers == {}


def test_parse_headers_env_missing(monkeypatch):
    """Test _parse_headers_env returns empty dict if env var is missing."""
    monkeypatch.delenv("MCP_JUPYTER_HEADERS_JSON", raising=False)
    headers = _parse_headers_env()
    assert headers == {}
