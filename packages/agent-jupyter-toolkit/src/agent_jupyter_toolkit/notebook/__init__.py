"""
Jupyter notebook manipulation toolkit.

This package provides high-level interfaces for working with Jupyter notebooks
across different storage backends and collaboration systems. It includes transport
abstractions, session management, cell manipulation utilities, and output handling.

Key Components:
- NotebookSession: High-level notebook manipulation interface
- NotebookWorkspace: Multi-notebook lifecycle and default selection
- NotebookDocumentTransport: Storage/collaboration backend protocol
- Factory functions: Automatic transport selection and configuration
- Cell utilities: Create and manipulate notebook cells
- Output handling: Process and format notebook execution outputs
"""

from . import utils
from .batch import execute_notebook_batch
from .buffer import NotebookBuffer
from .cells import create_code_cell, create_markdown_cell
from .factory import CollaborationMode, make_document_transport
from .session import NotebookSession
from .transport import NotebookDocumentTransport
from .trust import NotebookTrustResult, inspect_notebook_trust
from .types import (
    CellDeletedError,
    CellRunResult,
    CellSourceChangedError,
    NotebookCodeExecutionResult,
    NotebookMarkdownCellResult,
    NotebookPersistenceError,
    RunAllResult,
)
from .workspace import NotebookWorkspace, NotebookWorkspaceConfig

__all__ = [
    "make_document_transport",
    "CollaborationMode",
    "execute_notebook_batch",
    "inspect_notebook_trust",
    "NotebookDocumentTransport",
    "NotebookSession",
    "NotebookWorkspace",
    "NotebookWorkspaceConfig",
    "NotebookBuffer",
    "create_code_cell",
    "create_markdown_cell",
    "NotebookCodeExecutionResult",
    "NotebookMarkdownCellResult",
    "CellRunResult",
    "RunAllResult",
    "NotebookPersistenceError",
    "NotebookTrustResult",
    "CellDeletedError",
    "CellSourceChangedError",
    "utils",
]
