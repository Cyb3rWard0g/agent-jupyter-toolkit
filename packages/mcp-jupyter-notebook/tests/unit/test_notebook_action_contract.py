"""Keep the existing tool contract aligned with the transport-neutral actions."""

import inspect

from agent_jupyter_toolkit.actions import NotebookActions
from mcp_jupyter_notebook._mcp import FastMCP
from mcp_jupyter_notebook.tools import register_notebook_tools


def test_selected_tools_preserve_action_descriptions_parameters_and_defaults():
    server = FastMCP("action-contract")
    register_notebook_tools(server)
    for definition in NotebookActions.definitions():
        tool = server._tool_manager._tools[f"notebook_{definition.name}"]
        method = getattr(NotebookActions, definition.name)
        assert inspect.getdoc(tool.fn) == inspect.getdoc(method), definition.name
        shared = {
            name: arg
            for name, arg in inspect.signature(method).parameters.items()
            if name != "self"
        }
        wrapped = {
            name: arg
            for name, arg in inspect.signature(tool.fn).parameters.items()
            if name != "ctx"
        }
        assert shared == wrapped, definition.name
        # MCP accepts its normal coercions; the direct action dispatcher is strict.
        # Properties/defaults/required fields remain one public callable contract.
        assert tool.parameters["properties"] == definition.input_schema["properties"]
        assert tool.parameters.get("required", []) == definition.input_schema.get("required", [])
