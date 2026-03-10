"""
Canonical dataclasses and types for the notebook subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NotebookCodeExecutionResult:
    """
    Result of executing code in a notebook session (includes cell information).

    This extends ExecutionResult with notebook-specific information like
    cell index and enhanced output processing for AI agents.
    """

    status: str = "ok"
    execution_count: int | None = None
    cell_index: int = -1
    stdout: str = ""
    stderr: str = ""
    outputs: list[dict[str, Any]] = field(default_factory=list)

    # Enhanced fields for AI agents
    text_outputs: list[str] = field(default_factory=list)
    formatted_output: str = ""
    error_message: str | None = None
    elapsed_seconds: float | None = None


@dataclass
class NotebookMarkdownCellResult:
    """
    Result of inserting a markdown cell in a notebook session.
    Structured for robust agent workflows and error handling.
    """

    status: str = "ok"  # "ok" or "error"
    cell_index: int | None = None  # Index of the inserted cell, or None on error
    error_message: str | None = None  # Error message if insertion failed
    elapsed_seconds: float | None = None  # Time taken for insertion


@dataclass
class CellRunResult:
    """Per-cell result emitted by :pymeth:`NotebookSession.run_all`.

    Each item describes the outcome of executing (or skipping) a single
    code cell during a full-notebook run.
    """

    index: int
    """Zero-based cell index in the notebook."""

    cell_id: str | None = None
    """Stable cell identifier (``None`` if the cell has no ``id`` field)."""

    status: str = "ok"
    """``"ok"``, ``"error"``, or ``"skipped"``."""

    source_snippet: str = ""
    """First 100 characters of the cell source for quick identification."""

    execution_count: int | None = None
    """Kernel execution count after running the cell (``None`` if skipped)."""

    error_message: str | None = None
    """Formatted traceback / error text when ``status == "error"``."""

    elapsed_seconds: float | None = None
    """Wall-clock time for this individual cell execution."""


@dataclass
class RunAllResult:
    """Aggregate result returned by :pymeth:`NotebookSession.run_all`.

    Provides both summary statistics and a per-cell breakdown so agents and
    CI pipelines can programmatically inspect the outcome.
    """

    status: str = "ok"
    """``"ok"`` if every cell succeeded, ``"error"`` otherwise."""

    executed_count: int = 0
    """Number of code cells that were actually executed."""

    skipped_count: int = 0
    """Number of code cells skipped (empty source / non-code)."""

    cells: list[CellRunResult] = field(default_factory=list)
    """Per-cell results in notebook order."""

    first_failure: CellRunResult | None = None
    """The first cell that failed (``None`` if all succeeded)."""

    elapsed_seconds: float | None = None
    """Total wall-clock time for the entire run."""
