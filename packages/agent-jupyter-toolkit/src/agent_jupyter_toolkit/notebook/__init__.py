"""
Jupyter notebook manipulation toolkit.

This package provides high-level interfaces for working with Jupyter notebooks
across different storage backends and collaboration systems. It includes transport
abstractions, session management, cell manipulation utilities, and output handling.

Key Components:
- NotebookSession: High-level notebook manipulation interface
- NotebookDocumentTransport: Storage/collaboration backend protocol
- Factory functions: Automatic transport selection and configuration
- Cell utilities: Create and manipulate notebook cells
- Output handling: Process and format notebook execution outputs
"""

from . import utils
from .buffer import NotebookBuffer
from .cells import create_code_cell, create_markdown_cell
from .factory import make_document_transport
from .session import NotebookSession
from .transport import NotebookDocumentTransport
from .types import NotebookCodeExecutionResult, NotebookMarkdownCellResult

__all__ = [
    "make_document_transport",
    "NotebookDocumentTransport",
    "NotebookSession",
    "NotebookBuffer",
    "create_code_cell",
    "create_markdown_cell",
    "NotebookCodeExecutionResult",
    "NotebookMarkdownCellResult",
    "utils",
]
