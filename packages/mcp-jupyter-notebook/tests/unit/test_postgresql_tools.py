"""Unit tests for PostgreSQL tool registration.

These tests only validate that tools are registered when requested.
Behavioral tests (actual DB connections/queries) are intentionally out of
scope for unit tests.
"""

from mcp.server.fastmcp import FastMCP

from mcp_jupyter_notebook.tools import register_postgresql_tools


def test_register_postgresql_tools_registers_expected_tools():
    mcp = FastMCP("test")
    register_postgresql_tools(mcp)

    tool_names = {t.name for t in mcp._tool_manager.list_tools()}

    assert "postgresql_connect" in tool_names
    assert "postgresql_test_connection" in tool_names
    assert "postgresql_close" in tool_names
    assert "postgresql_query_to_df" in tool_names
    assert "postgresql_schema_list_tables" in tool_names
    assert "postgresql_schema_list_columns" in tool_names
    assert "postgresql_schema_tree" in tool_names
    assert "postgresql_reset" in tool_names
