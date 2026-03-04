"""
Comprehensive notebook utility functions.

This module provides all utility functions for working with Jupyter notebooks,
including file operations, path validation, output normalization, and notebook
creation. It consolidates functionality that was previously split across
multiple utility modules.

Key Features:
- Notebook loading, saving, and validation
- Path security and validation utilities
- Output format normalization for nbformat compatibility
- Remote notebook creation via Contents API
- Atomic file operations for safety

Example:
    ```python
    from agent_jupyter_toolkit.notebook.utils import (
        load_notebook, save_notebook, ensure_allowed,
        to_nbformat_outputs, create_notebook_via_contents_api
    )

    # Load and validate a notebook
    nb = load_notebook(Path("analysis.ipynb"))

    # Normalize execution outputs
    normalized = to_nbformat_outputs(execution_result)

    # Save with validation
    save_notebook(nb, Path("output.ipynb"))
    ```
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
import nbformat

from ..kernel.types import ExecutionResult
from .config import CFG

logger = logging.getLogger(__name__)


# =====================================
# Path Validation and Security
# =====================================


def _is_within(child: Path, parent: Path) -> bool:
    """Check if child path is within parent directory."""
    # Both should be absolute, resolved
    child_r = child.resolve()
    parent_r = parent.resolve()
    try:
        # Python 3.9+: Path.is_relative_to
        return child_r.is_relative_to(parent_r)  # type: ignore[attr-defined]
    except Exception:
        # Fallback: commonpath
        return os.path.commonpath([str(child_r), str(parent_r)]) == str(parent_r)


def ensure_allowed(path: Path) -> Path:
    """
    Validate that an existing path is inside an allowed root.

    Args:
        path: Path to validate

    Returns:
        Resolved absolute path on success

    Raises:
        PermissionError: If path is not within allowed roots

    Example:
        ```python
        safe_path = ensure_allowed(Path("notebooks/analysis.ipynb"))
        ```
    """
    p = path.expanduser().resolve()
    for root in CFG.allowed_roots:
        if _is_within(p, root):
            return p
    logger.error("Path not allowed: %s", p)
    raise PermissionError(f"Path not allowed: {p}")


def ensure_allowed_for_write(path: Path) -> Path:
    """
    Validate that a target path (which may not exist yet) is inside an allowed root.

    Checks the parent directory for permission validation.

    Args:
        path: Target path to validate for writing

    Returns:
        Resolved absolute path on success

    Raises:
        PermissionError: If write path is not within allowed roots

    Example:
        ```python
        safe_path = ensure_allowed_for_write(Path("output/new_notebook.ipynb"))
        ```
    """
    p = path.expanduser().resolve()
    parent = p if p.is_dir() else p.parent
    for root in CFG.allowed_roots:
        if _is_within(parent, root):
            return p
    logger.error("Write path not allowed: %s", p)
    raise PermissionError(f"Write path not allowed: {p}")


# =====================================
# Output Normalization Utilities
# =====================================


def to_nbformat_outputs(obj: Any) -> list[dict[str, Any]]:
    """
    Normalize various result shapes to nbformat-style outputs (list[dict]).

    This function handles multiple input types and converts them to a standardized
    list of nbformat-compliant output dictionaries. It's the primary interface
    for converting kernel execution results to notebook cell outputs.

    Args:
        obj: The object to normalize. Can be ExecutionResult, dict, list, or other.

    Returns:
        List[Dict[str, Any]]: Normalized nbformat-style outputs ready for notebook cells.
        Each dict contains keys like 'output_type', 'data', 'metadata', etc.

    Accepts:
        - ExecutionResult: Returns its .outputs, normalized with execution_count filled
        - dict with 'outputs': Returns that outputs list (or empty list)
        - list of dicts: Assumes already nbformat-like, applies light normalization
        - anything else: Returns empty list

    Example:
        ```python
        # From ExecutionResult
        result = ExecutionResult(outputs=[{"output_type": "stream", "text": "Hello"}])
        outputs = to_nbformat_outputs(result)

        # From dict
        notebook_cell = {"outputs": [{"output_type": "execute_result", "data": {}}]}
        outputs = to_nbformat_outputs(notebook_cell)

        # From raw list (already normalized)
        raw_outputs = [{"output_type": "error", "ename": "ValueError"}]
        outputs = to_nbformat_outputs(raw_outputs)
        ```

    Note:
        This function performs light normalization to ensure compatibility across
        different Jupyter implementations and versions.
    """
    # ExecutionResult: pass-through (with light normalization)
    if isinstance(obj, ExecutionResult):
        return _normalize_outputs(obj.outputs or [], exec_count=obj.execution_count)

    # Dict with 'outputs' (e.g., model fetch)
    if isinstance(obj, dict) and "outputs" in obj:
        outs = obj.get("outputs") or []
        # Assume nbformat already; keep light normalization to be safe
        return _normalize_outputs(outs, exec_count=obj.get("execution_count"))

    # A list that already looks nbformat-ish (each item has 'output_type')
    if isinstance(obj, list) and (not obj or isinstance(obj[0], dict) and "output_type" in obj[0]):
        return _normalize_outputs(obj, exec_count=None)

    return []


def _normalize_outputs(
    outs: list[dict[str, Any]], *, exec_count: int | None
) -> list[dict[str, Any]]:
    """
    Apply nbformat normalization to a list of output dictionaries.

    This internal function performs the detailed work of normalizing outputs to
    ensure they conform to nbformat standards and are compatible across different
    Jupyter implementations.

    Normalization includes:
        - Map 'update_display_data' -> 'display_data'
        - Ensure required keys exist (data/metadata for display types)
        - Fill execution_count on execute_result if missing
        - Standardize stream/error output formats
        - Filter out transient/internal message types

    Args:
        outs: List of raw output dictionaries to normalize
        exec_count: Execution count to use for execute_result outputs if missing

    Returns:
        List[Dict[str, Any]]: Normalized outputs ready for nbformat compliance

    Note:
        This function is internal and should not be called directly. Use
        to_nbformat_outputs() instead.
    """
    norm: list[dict[str, Any]] = []

    for o in outs or []:
        ot = o.get("output_type")

        if ot == "stream":
            norm.append(
                {
                    "output_type": "stream",
                    "name": o.get("name"),
                    "text": o.get("text", "") or "",
                }
            )
            continue

        if ot in ("display_data", "update_display_data", "execute_result"):
            mapped_type = "display_data" if ot == "update_display_data" else ot
            entry: dict[str, Any] = {
                "output_type": mapped_type,
                "data": o.get("data") or {},
                "metadata": o.get("metadata") or {},
            }
            if mapped_type == "execute_result":
                entry["execution_count"] = o.get("execution_count", exec_count)
            norm.append(entry)
            continue

        if ot == "error":
            norm.append(
                {
                    "output_type": "error",
                    "ename": o.get("ename"),
                    "evalue": o.get("evalue"),
                    "traceback": o.get("traceback") or [],
                }
            )
            continue

        # Ignore other/transient frame types here (e.g., clear_output handled upstream)

    return norm


# =====================================
# Core Notebook Operations
# =====================================


def validate_notebook(nb: nbformat.NotebookNode) -> None:
    """
    Validate a notebook using nbformat validation.

    Args:
        nb: Notebook to validate

    Raises:
        Exception: If validation fails

    Example:
        ```python
        validate_notebook(notebook)
        ```
    """
    try:
        nbformat.validate(nb)
    except Exception as e:
        logger.error("Notebook validation failed: %s", e)
        raise


def atomic_write_notebook(nb: nbformat.NotebookNode, path: Path) -> None:
    """
    Write notebook atomically using temporary file.

    Args:
        nb: Notebook to write
        path: Target path

    Raises:
        Exception: If write operation fails

    Example:
        ```python
        atomic_write_notebook(notebook, Path("output.ipynb"))
        ```
    """
    target = ensure_allowed_for_write(path)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        nbformat.write(nb, tmp)
        tmp.replace(target)
        logger.info("Notebook written atomically to %s", target)
    except Exception as e:
        logger.error("Failed to write notebook atomically to %s: %s", target, e)
        raise


def load_notebook(path: Path) -> nbformat.NotebookNode:
    """
    Load a notebook from disk with path validation.

    Args:
        path: Path to notebook file

    Returns:
        Loaded notebook

    Raises:
        PermissionError: If path is not allowed
        Exception: If loading fails

    Example:
        ```python
        nb = load_notebook(Path("analysis.ipynb"))
        ```
    """
    p = ensure_allowed(path)
    try:
        nb = nbformat.read(p, as_version=4)
        logger.info("Notebook loaded from %s", p)
        return nb
    except Exception as e:
        logger.error("Failed to load notebook from %s: %s", p, e)
        raise


def save_notebook(nb: nbformat.NotebookNode, path: Path, validate: bool = True) -> None:
    """
    Save a notebook to disk with optional validation.

    Args:
        nb: Notebook to save
        path: Target path for saving
        validate: Whether to validate notebook before saving

    Raises:
        PermissionError: If path is not allowed
        Exception: If validation or saving fails

    Example:
        ```python
        save_notebook(notebook, Path("output.ipynb"), validate=True)
        ```
    """
    # Convert outputs to NotebookNode objects for nbformat compatibility
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code" and "outputs" in cell:
            cell["outputs"] = [
                nbformat.from_dict(out) if not isinstance(out, nbformat.NotebookNode) else out
                for out in cell["outputs"]
            ]
    if validate:
        validate_notebook(nb)
    atomic_write_notebook(nb, path)


# =====================================
# Notebook Creation Utilities
# =====================================


def create_minimal_notebook_content() -> dict[str, Any]:
    """
    Create the standard minimal notebook content structure.

    This is the same structure used by both Contents API and Collaboration transports.

    Returns:
        Dictionary containing minimal notebook structure
    """
    return {"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}


async def create_notebook_via_contents_api(
    base_url: str,
    path: str,
    *,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    check_exists: bool = True,
) -> bool:
    """
    Create a minimal notebook using Jupyter Contents API.

    This is a standalone utility that can be used before starting any transport.
    It's particularly useful for collaboration where the notebook needs to exist
    before joining the collaboration room.

    Args:
        base_url: Jupyter server base URL (e.g., "http://localhost:8888")
        path: Notebook path relative to server root (e.g., "analysis.ipynb")
        token: Optional API token
        headers: Optional extra headers
        timeout: Request timeout in seconds
        check_exists: If True, check if notebook already exists first

    Returns:
        True if notebook was created, False if it already existed

    Raises:
        RuntimeError: If creation fails

    Example:
        # Create notebook before starting collaboration
        created = await create_notebook_via_contents_api(
            "http://localhost:8888",
            "shared_analysis.ipynb",
            token="abc123"
        )
        if created:
            print("Notebook created successfully")
    """
    # Prepare headers
    http_headers = {"Accept": "application/json"}
    if headers:
        http_headers.update(headers)
    if token:
        http_headers["Authorization"] = f"Token {token}"

    async with aiohttp.ClientSession(headers=http_headers) as session:
        url = f"{base_url.rstrip('/')}/api/contents/{quote(path, safe='')}"

        # Check if notebook already exists
        if check_exists:
            async with session.get(url, timeout=timeout) as resp:
                if resp.status == 200:
                    logger.debug("Notebook already exists: %s", path)
                    return False
                elif resp.status != 404:
                    text = await resp.text()
                    raise RuntimeError(
                        f"Failed to check notebook existence ({resp.status}): {text}"
                    )

        # Create the notebook
        body = {
            "type": "notebook",
            "format": "json",
            "content": create_minimal_notebook_content(),
        }

        async with session.put(url, json=body, timeout=timeout) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise RuntimeError(f"Failed to create notebook ({resp.status}): {text}")

        logger.info("Notebook created via Contents API: %s", path)
        return True
