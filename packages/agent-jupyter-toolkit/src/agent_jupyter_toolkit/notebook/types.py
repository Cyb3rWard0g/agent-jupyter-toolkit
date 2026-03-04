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
