from __future__ import annotations

import uuid as _uuid_mod
from typing import Any

import pycrdt


def ytext_to_str(v: Any) -> str:
    """
    Convert a pycrdt Text, plain string, or other value to a Python string.

    Args:
        v: Value that might be a pycrdt.Text, str, or another type.

    Returns:
        String content. Empty string if *v* is not text-like.
    """
    if isinstance(v, pycrdt.Text):
        return str(v)
    return v if isinstance(v, str) else ""


def _uuid() -> str:
    """Generate a random hex string suitable for nbformat 'id'."""
    return _uuid_mod.uuid4().hex


def validate_tags(tags: list[str]) -> None:
    """Ensure tags is a list of strings (raise TypeError otherwise)."""
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise TypeError("tags must be a list of strings")


def make_code_cell_dict(
    source: str, metadata: dict[str, Any] | None, tags: list[str] | None
) -> dict[str, Any]:
    """
    Create a code cell dictionary compatible with nbformat.

    Args:
        source: Source code for the cell
        metadata: Optional metadata dict to merge into the cell
        tags: Optional list of tags to add to metadata

    Returns:
        Dictionary representing a code cell in nbformat structure
    """
    md = dict(metadata or {})
    if tags:
        md = dict(md)
        md["tags"] = list(set(tags))
    return {
        "id": _uuid(),
        "cell_type": "code",
        "metadata": md,
        "source": source,
        "outputs": [],
        "execution_count": None,
    }


def make_md_cell_dict(source: str, tags: list[str] | None = None) -> dict[str, Any]:
    """
    Create a markdown cell dictionary compatible with nbformat.

    Args:
        source: Markdown text for the cell
        tags: Optional list of tags to add to metadata

    Returns:
        Dictionary representing a markdown cell in nbformat structure
    """
    md: dict[str, Any] = {}
    if tags:
        md["tags"] = list(set(tags))
    return {
        "id": _uuid(),
        "cell_type": "markdown",
        "metadata": md,
        "source": source,
    }
