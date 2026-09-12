"""
Unit tests for server.py utilities and entrypoints.
"""

from types import SimpleNamespace
from unittest.mock import create_autospec

import pytest

from mcp_jupyter_notebook._mcp import FastMCP
from mcp_jupyter_notebook.server import _parse_headers_env, process_config


def _config_args(**overrides):
    values = {
        "mode": None,
        "base_url": None,
        "token": None,
        "kernel_name": None,
        "notebook_path": None,
        "transport": None,
        "host": None,
        "port": None,
        "collaboration_mode": None,
        "enable_tools": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


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


def test_collaboration_mode_config_and_legacy_alias(monkeypatch):
    monkeypatch.setenv("MCP_JUPYTER_COLLABORATION_MODE", "required")
    assert process_config(_config_args())["collaboration_mode"] == "required"

    monkeypatch.delenv("MCP_JUPYTER_COLLABORATION_MODE")
    monkeypatch.setenv("MCP_JUPYTER_PREFER_COLLAB", "false")
    config = process_config(_config_args())
    assert config["collaboration_mode"] == "disabled"
    assert config["prefer_collab"] is False


def test_unknown_collaboration_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("MCP_JUPYTER_COLLABORATION_MODE", "sometimes")
    with pytest.raises(ValueError, match="collaboration mode"):
        process_config(_config_args())


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["sse", "streamable-http"])
async def test_http_transport_uses_supported_sdk_bind_configuration(monkeypatch, transport):
    from mcp_jupyter_notebook._mcp import MCP_V2, run_http_server

    server = FastMCP("http-compatibility-test")
    name = "run_sse_async" if transport == "sse" else "run_streamable_http_async"
    # Autospec validates the actual installed SDK's signature without binding a port.
    run = create_autospec(getattr(server, name))
    monkeypatch.setattr(server, name, run)
    await run_http_server(server, transport, host="127.0.0.1", port=8123)
    if MCP_V2:
        run.assert_awaited_once_with(host="127.0.0.1", port=8123)
    else:
        run.assert_awaited_once_with()
        assert server.settings.host == "127.0.0.1"
        assert server.settings.port == 8123
