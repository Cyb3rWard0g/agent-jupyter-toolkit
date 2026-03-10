"""Shared helpers for the core notebook MCP tool pack."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mcp.server.fastmcp import Context


def get_manager(ctx: Context):
    """Extract the session manager from the MCP lifespan context."""
    return ctx.request_context.lifespan_context.manager


def get_session(ctx: Context, notebook_path: str | None = None):
    """Resolve a notebook session from an optional notebook path."""
    return get_manager(ctx).get(notebook_path)


def code_result(result: Any) -> dict[str, Any]:
    """Normalize a notebook code execution result."""
    return {
        "ok": result.status == "ok",
        "cell_id": getattr(result, "cell_id", None),
        "cell_index": result.cell_index,
        "execution_count": result.execution_count,
        "status": result.status,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "outputs": result.outputs,
        "text_outputs": result.text_outputs,
        "formatted_output": result.formatted_output,
        "error_message": result.error_message,
        "elapsed_seconds": result.elapsed_seconds,
    }


def run_all_result(result: Any) -> dict[str, Any]:
    """Normalize a run-all result dataclass."""
    payload = asdict(result)
    payload["ok"] = result.status == "ok"
    return payload


def normalize_source(source: Any) -> str:
    """Convert nbformat source values into a plain string."""
    if isinstance(source, list):
        return "".join(str(part) for part in source)
    return "" if source is None else str(source)


def summarize_cell(cell: dict[str, Any], index: int) -> dict[str, Any]:
    """Create a compact, agent-friendly notebook cell summary."""
    return {
        "index": index,
        "cell_id": cell.get("id"),
        "cell_type": cell.get("cell_type"),
        "source": normalize_source(cell.get("source", "")),
        "execution_count": cell.get("execution_count"),
        "outputs_count": len(cell.get("outputs", [])),
    }


async def cell_id_for_index(session: Any, cell_index: int | None) -> str | None:
    """Best-effort lookup of a stable cell ID from a positional index."""
    if cell_index is None or cell_index < 0:
        return None
    try:
        cell = await session.get_cell(cell_index)
    except Exception:
        return None
    return cell.get("id")


async def cell_id_map_for_indices(session: Any, cell_indices: set[int]) -> dict[int, str | None]:
    """Resolve multiple cell IDs with a single notebook fetch."""
    if not cell_indices:
        return {}

    try:
        notebook = await session.doc.fetch()
    except Exception:
        return {}

    cells = notebook.get("cells") or []
    if not isinstance(cells, list):
        return {}

    ids: dict[int, str | None] = {}
    for index in cell_indices:
        if 0 <= index < len(cells):
            ids[index] = cells[index].get("id")
    return ids


async def enriched_code_result(session: Any, result: Any) -> dict[str, Any]:
    """Return a code-cell result payload enriched with the stable cell ID."""
    payload = code_result(result)
    payload["cell_id"] = await cell_id_for_index(session, payload.get("cell_index"))
    return payload


def move_before_index(from_index: int, anchor_index: int) -> int:
    """Return the final target index for a move-before operation."""
    if from_index == anchor_index:
        return from_index
    if from_index < anchor_index:
        return anchor_index - 1
    return anchor_index


def move_after_index(from_index: int, anchor_index: int) -> int:
    """Return the final target index for a move-after operation."""
    if from_index == anchor_index:
        return from_index
    if from_index < anchor_index:
        return anchor_index
    return anchor_index + 1
