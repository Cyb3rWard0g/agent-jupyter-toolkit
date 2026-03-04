"""
Cell creation utilities for Jupyter notebooks.

This module provides functions to create well-formed notebook cells that are
compliant with the nbformat schema. These utilities are primarily used by
notebook transports and the NotebookSession for cell creation.
"""

from typing import Any

import nbformat
from nbformat import NotebookNode


def create_code_cell(
    source: Any,
    metadata: dict[str, Any] | None = None,
    outputs: Any | None = None,
    execution_count: int | None = None,
) -> NotebookNode:
    """
    Create a new code cell using nbformat's schema-compliant helper.

    This function creates a properly formatted Jupyter code cell that can be
    executed and will display outputs. It ensures compliance with the nbformat
    specification.

    Args:
        source: The code for the cell. Can be a string or list of strings.
                If a list, it will be joined into a single source string.
        metadata: Optional metadata dict for the cell. Common keys include:
                 - "tags": List of cell tags
                 - "collapsed": Whether the cell is collapsed
                 - "scrolled": Whether the output is scrolled
        outputs: Optional list of output objects from previous execution.
                Defaults to an empty list for new cells.
        execution_count: Optional execution count from kernel execution.
                        None for unexecuted cells.

    Returns:
        NotebookNode: A new code cell ready for use in a notebook.

    Example:
        ```python
        cell = create_code_cell(
            source="import pandas as pd\\ndf = pd.DataFrame({'a': [1, 2, 3]})",
            metadata={"tags": ["data-loading"]},
            execution_count=1
        )
        ```
    """
    if outputs is None:
        outputs = []

    return nbformat.v4.new_code_cell(
        source=source, metadata=metadata or {}, outputs=outputs, execution_count=execution_count
    )


def create_markdown_cell(source: Any, metadata: dict[str, Any] | None = None) -> NotebookNode:
    """
    Create a new markdown cell using nbformat's schema-compliant helper.

    This function creates a properly formatted Jupyter markdown cell for
    documentation, explanations, and rich text content. It ensures compliance
    with the nbformat specification.

    Args:
        source: The markdown content for the cell. Can be a string or list of strings.
               Supports full Markdown syntax including headers, links, images, etc.
        metadata: Optional metadata dict for the cell. Common keys include:
                 - "tags": List of cell tags
                 - "collapsed": Whether the cell is collapsed

    Returns:
        NotebookNode: A new markdown cell ready for use in a notebook.

    Example:
        ```python
        cell = create_markdown_cell(
            source="# Analysis Results\\n\\nThis section shows...",
            metadata={"tags": ["documentation", "results"]}
        )
        ```
    """
    return nbformat.v4.new_markdown_cell(source=source, metadata=metadata or {})
