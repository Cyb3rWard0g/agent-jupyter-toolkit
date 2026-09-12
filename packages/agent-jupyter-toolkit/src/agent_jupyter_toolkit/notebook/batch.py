"""Optional nbclient-backed batch notebook execution."""

from __future__ import annotations

import json
from typing import Any

import nbformat


async def execute_notebook_batch(
    notebook: dict[str, Any],
    *,
    kernel_name: str = "python3",
    timeout: int | None = 120,
    allow_errors: bool = False,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Execute a copied notebook with a dedicated nbclient-owned kernel.

    ``timeout`` is an integer number of seconds per cell, as required by nbclient.
    """
    try:
        from nbclient import NotebookClient
    except ImportError as exc:
        raise RuntimeError(
            "Batch execution requires the 'agent-jupyter-toolkit[batch]' extra"
        ) from exc

    node = nbformat.from_dict(json.loads(json.dumps(notebook)))
    nbformat.validate(node)
    options = {
        "kernel_name": kernel_name,
        "timeout": timeout,
        "allow_errors": allow_errors,
    }
    if cwd:
        options["resources"] = {"metadata": {"path": cwd}}
    client = NotebookClient(node, **options)
    executed = await client.async_execute()
    nbformat.validate(executed)
    return json.loads(nbformat.writes(executed, version=4, split_lines=False))
