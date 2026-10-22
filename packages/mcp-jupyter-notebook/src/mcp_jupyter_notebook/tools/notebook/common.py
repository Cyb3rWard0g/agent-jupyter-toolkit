"""MCP context resolution and compatible shared result helpers."""

from agent_jupyter_toolkit.actions import NotebookActions
from agent_jupyter_toolkit.actions._results import (
    cell_id_for_index as cell_id_for_index,
)
from agent_jupyter_toolkit.actions._results import (
    cell_id_map_for_indices as cell_id_map_for_indices,
)
from agent_jupyter_toolkit.actions._results import (
    code_result as code_result,
)
from agent_jupyter_toolkit.actions._results import (
    enriched_code_result as enriched_code_result,
)
from agent_jupyter_toolkit.actions._results import (
    move_after_index as move_after_index,
)
from agent_jupyter_toolkit.actions._results import (
    move_before_index as move_before_index,
)
from agent_jupyter_toolkit.actions._results import (
    normalize_source as normalize_source,
)
from agent_jupyter_toolkit.actions._results import (
    run_all_result as run_all_result,
)
from agent_jupyter_toolkit.actions._results import (
    summarize_cell as summarize_cell,
)
from mcp_jupyter_notebook._mcp import Context

_ACTION_DESCRIPTIONS = {item.name: item.description for item in NotebookActions.definitions()}


def shared_action(name: str):
    """Use the core callable description before MCP derives its tool metadata."""
    description = _ACTION_DESCRIPTIONS[name]

    def annotate(function):
        function.__doc__ = description
        return function

    return annotate


def get_manager(ctx: Context):
    """Extract the session manager from the MCP lifespan context."""
    return ctx.request_context.lifespan_context.manager


def get_session(ctx: Context, notebook_path: str | None = None):
    """Resolve a notebook session from an optional notebook path."""
    return get_manager(ctx).get(notebook_path)
