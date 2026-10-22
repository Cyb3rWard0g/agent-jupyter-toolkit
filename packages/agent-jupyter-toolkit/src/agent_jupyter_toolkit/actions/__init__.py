"""Named notebook actions, independent of MCP or any agent framework.

Install ``agent-jupyter-toolkit[actions]`` to use this optional facade. The
caller owns workspace lifecycle, authorization, serialization and retry policy.
Action metadata describes the SDK interface; it does not authorize execution.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from inspect import Parameter, getdoc, signature
from typing import Any, get_type_hints

from pydantic import BaseModel, ConfigDict, create_model

from ..kernel.variables import VariableManager
from ..notebook import NotebookWorkspace
from ..utils import (
    check_package_availability,
    get_session_info,
    get_variable_value,
    get_variables,
    invoke_code_cell,
    invoke_markdown_cell,
)
from ._results import cell_id_for_index, enriched_code_result, normalize_source, summarize_cell


@dataclass(frozen=True)
class NotebookActionDefinition:
    """A named action and JSON input schema derived from its Python signature."""

    name: str
    title: str
    description: str
    input_schema: dict[str, Any]


class NotebookActions:
    """Reusable operations over a caller-owned, persistent NotebookWorkspace.

    This facade never opens/closes a workspace context around an individual
    invocation. Calls use the same session until the caller explicitly closes it.
    Injected primitives preserve adapter integrations and are caller-owned.
    """

    def __init__(
        self,
        workspace: NotebookWorkspace,
        *,
        invoke_code_cell_fn: Any = invoke_code_cell,
        invoke_markdown_cell_fn: Any = invoke_markdown_cell,
        check_package_availability_fn: Any = check_package_availability,
        get_session_info_fn: Any = get_session_info,
        get_variable_value_fn: Any = get_variable_value,
        get_variables_fn: Any = get_variables,
        variable_manager_cls: Any = VariableManager,
    ) -> None:
        self.workspace = workspace
        self._invoke_code_cell_fn = invoke_code_cell_fn
        self._invoke_markdown_cell_fn = invoke_markdown_cell_fn
        self._check_package_availability_fn = check_package_availability_fn
        self._get_session_info_fn = get_session_info_fn
        self._get_variable_value_fn = get_variable_value_fn
        self._get_variables_fn = get_variables_fn
        self._variable_manager_cls = variable_manager_cls

    async def open(self, notebook_path: str, set_default: bool = False) -> dict[str, Any]:
        """Open or reuse a notebook session; set_default switches subsequent pathless calls."""
        manager = self.workspace
        try:
            await manager.open(notebook_path)
            if set_default:
                manager.set_default(notebook_path)
            return {
                "ok": True,
                "notebook_path": notebook_path,
                "is_default": manager.is_default(notebook_path),
                "open_notebooks": manager.paths,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def close(self, notebook_path: str) -> dict[str, Any]:
        """Close a notebook session and release its resources."""
        manager = self.workspace
        closed = await manager.close(notebook_path)
        return {
            "ok": closed,
            "notebook_path": notebook_path,
            "default_notebook": manager.default_path,
            "open_notebooks": manager.paths,
        }

    async def list(self) -> dict[str, Any]:
        """List all currently open notebook sessions."""
        manager = self.workspace
        return {
            "ok": True,
            "default_notebook": manager.default_path,
            "notebooks": manager.list_sessions(),
        }

    async def markdown_add(
        self, content: str, notebook_path: str | None = None, position: int | None = None
    ) -> dict[str, Any]:
        """Add a markdown cell to the notebook."""
        session = self.workspace.get(notebook_path)
        result = await self._invoke_markdown_cell_fn(session, content, index=position)
        return {
            "ok": result.status == "ok",
            "cell_index": result.cell_index,
            "cell_id": await cell_id_for_index(session, result.cell_index),
            "error_message": result.error_message,
            "elapsed_seconds": result.elapsed_seconds,
        }

    async def read(self, notebook_path: str | None = None) -> dict[str, Any]:
        """Read cell summaries and document metadata; use cell reads for full outputs."""
        session = self.workspace.get(notebook_path)
        notebook = await session.doc.fetch()
        cells_summary = [
            summarize_cell(cell, index) for index, cell in enumerate(notebook.get("cells", []))
        ]
        return {
            "ok": True,
            "cell_count": len(cells_summary),
            "cells": cells_summary,
            "metadata": notebook.get("metadata", {}),
        }

    async def cell_read_by_id(
        self, cell_id: str, notebook_path: str | None = None
    ) -> dict[str, Any]:
        """Read a single cell using its stable Jupyter cell ID."""
        session = self.workspace.get(notebook_path)
        try:
            cell = await session.doc.get_cell_by_id(cell_id)
            return {"ok": True, "cell_id": cell_id, "cell": cell}
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}

    async def cell_source_by_id(
        self, cell_id: str, notebook_path: str | None = None
    ) -> dict[str, Any]:
        """Read only the source text of a cell using its stable ID."""
        session = self.workspace.get(notebook_path)
        try:
            cell = await session.doc.get_cell_by_id(cell_id)
            return {
                "ok": True,
                "cell_id": cell_id,
                "source": normalize_source(cell.get("source", "")),
            }
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}

    async def cell_source_set_by_id(
        self, cell_id: str, source: str, notebook_path: str | None = None
    ) -> dict[str, Any]:
        """Replace a cell's source using its stable ID without executing it."""
        session = self.workspace.get(notebook_path)
        try:
            cell_index = await session.doc.resolve_cell_index(cell_id)
            await session.doc.set_cell_source(cell_index, source)
            return {"ok": True, "cell_id": cell_id, "cell_index": cell_index, "source": source}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def code_run(
        self, code: str, notebook_path: str | None = None, timeout: float = 120.0
    ) -> dict[str, Any]:
        """Append a new code cell to the notebook, execute it, and return outputs."""
        session = self.workspace.get(notebook_path)
        result = await self._invoke_code_cell_fn(session, code, timeout=timeout)
        return await enriched_code_result(session, result)

    async def packages_check(
        self, packages: list[str], notebook_path: str | None = None
    ) -> dict[str, Any]:
        """Check which packages are available in the kernel without installing."""
        session = self.workspace.get(notebook_path)
        availability = await self._check_package_availability_fn(session.kernel, packages)
        return {"ok": True, "packages": availability}

    async def dependencies_list(self, notebook_path: str | None = None) -> dict[str, Any]:
        """List packages that have been installed and tracked in the notebook metadata.

        Returns the dependency manifest stored under notebook.metadata.agent_dependencies,
        showing each package's version and when it was installed.
        """
        session = self.workspace.get(notebook_path)
        deps = await session.get_tracked_dependencies()
        return {"ok": True, "dependencies": deps, "count": len(deps)}

    async def kernel_info(self, notebook_path: str | None = None) -> dict[str, Any]:
        """Get detailed kernel metadata."""
        session = self.workspace.get(notebook_path)
        info = await session.kernel.kernel_info()
        return {
            "ok": info.status == "ok",
            "protocol_version": info.protocol_version,
            "implementation": info.implementation,
            "implementation_version": info.implementation_version,
            "language_info": info.language_info,
            "banner": info.banner,
            "help_links": info.help_links,
            "supported_features": info.supported_features,
            "raw_content": info.raw_content,
        }

    async def session_info(self, notebook_path: str | None = None) -> dict[str, Any]:
        """Get information about the current notebook session."""
        session = self.workspace.get(notebook_path)
        info = await self._get_session_info_fn(session.kernel)
        info.update(self.workspace.document_transport_info(session))
        return info

    async def inspect(
        self, code: str, cursor_pos: int, notebook_path: str | None = None, detail_level: int = 0
    ) -> dict[str, Any]:
        """Inspect an object in the kernel at the given cursor position."""
        session = self.workspace.get(notebook_path)
        result = await session.kernel.inspect(code, cursor_pos, detail_level=detail_level)
        return asdict(result)

    async def complete(
        self, code: str, cursor_pos: int, notebook_path: str | None = None
    ) -> dict[str, Any]:
        """Get tab-completion suggestions for code at the cursor position."""
        session = self.workspace.get(notebook_path)
        result = await session.kernel.complete(code, cursor_pos)
        return asdict(result)

    async def code_is_complete(self, code: str, notebook_path: str | None = None) -> dict[str, Any]:
        """Check whether a code fragment is syntactically complete."""
        session = self.workspace.get(notebook_path)
        result = await session.kernel.is_complete(code)
        return asdict(result)

    async def variables_list(self, notebook_path: str | None = None) -> dict[str, Any]:
        """List user-defined variable names in the kernel."""
        session = self.workspace.get(notebook_path)
        variables = await self._get_variables_fn(session.kernel)
        return {"ok": True, "variables": variables}

    async def variable_get(self, name: str, notebook_path: str | None = None) -> dict[str, Any]:
        """Read a JSON-compatible variable value; DataFrames require separate analysis."""
        session = self.workspace.get(notebook_path)
        value = await self._get_variable_value_fn(session.kernel, name)
        return {"ok": value is not None, "name": name, "value": value}

    async def variable_set(
        self, name: str, value: Any, notebook_path: str | None = None
    ) -> dict[str, Any]:
        """Set a variable in the kernel's global scope."""
        session = self.workspace.get(notebook_path)
        try:
            variable_manager = self._variable_manager_cls(session.kernel)
            await variable_manager.set(name, value)
            return {"ok": True, "name": name}
        except Exception as exc:
            return {"ok": False, "name": name, "error": str(exc)}

    @classmethod
    def definitions(cls) -> tuple[NotebookActionDefinition, ...]:
        """Return fresh descriptors; changing one cannot alter later catalogs."""
        return tuple(
            NotebookActionDefinition(
                name=name,
                title=title,
                description=getdoc(action) or "",
                input_schema=_input_model(name).model_json_schema(),
            )
            for name, (title, action) in _ACTIONS.items()
        )

    async def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate and invoke one explicitly supported action, without retries."""
        parsed = self.validate_arguments(name, arguments)
        return await _ACTIONS[name][1](self, **parsed)

    @staticmethod
    def validate_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate inputs before a host reserves or accesses an execution session."""
        if name not in _ACTIONS:
            raise ValueError(f"Unknown notebook action: {name}")
        parsed = _input_model(name).model_validate(arguments)
        return parsed.model_dump()


# This explicit registry is the complete dispatch surface. Do not resolve a
# caller-supplied attribute name or register every SDK method automatically.
_ACTIONS = {
    "cell_read_by_id": ("Read Cell By ID", NotebookActions.cell_read_by_id),
    "cell_source_by_id": ("Read Cell Source By ID", NotebookActions.cell_source_by_id),
    "cell_source_set_by_id": ("Set Cell Source By ID", NotebookActions.cell_source_set_by_id),
    "close": ("Close Notebook", NotebookActions.close),
    "code_is_complete": ("Check Code Completeness", NotebookActions.code_is_complete),
    "code_run": ("Run Code Cell", NotebookActions.code_run),
    "complete": ("Code Completion", NotebookActions.complete),
    "dependencies_list": ("List Tracked Dependencies", NotebookActions.dependencies_list),
    "inspect": ("Inspect Object", NotebookActions.inspect),
    "kernel_info": ("Kernel Info", NotebookActions.kernel_info),
    "list": ("List Open Notebooks", NotebookActions.list),
    "markdown_add": ("Add Markdown Cell", NotebookActions.markdown_add),
    "open": ("Open Notebook", NotebookActions.open),
    "packages_check": ("Check Package Availability", NotebookActions.packages_check),
    "read": ("Read Notebook", NotebookActions.read),
    "session_info": ("Session Info", NotebookActions.session_info),
    "variable_get": ("Get Variable", NotebookActions.variable_get),
    "variable_set": ("Set Variable", NotebookActions.variable_set),
    "variables_list": ("List Variables", NotebookActions.variables_list),
}


@lru_cache(maxsize=len(_ACTIONS))
def _input_model(name: str) -> type[BaseModel]:
    action = _ACTIONS[name][1]
    hints = get_type_hints(action)
    fields = {
        key: (hints[key], ... if parameter.default is Parameter.empty else parameter.default)
        for key, parameter in signature(action).parameters.items()
        if key != "self"
    }
    return create_model(
        f"Notebook_{name}_Input",
        __config__=ConfigDict(extra="forbid", strict=True, allow_inf_nan=False),
        **fields,
    )


__all__ = ["NotebookActionDefinition", "NotebookActions"]
