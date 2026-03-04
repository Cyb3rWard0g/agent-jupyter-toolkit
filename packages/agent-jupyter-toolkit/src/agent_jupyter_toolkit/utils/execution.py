"""
High-level execution utilities for AI agents.

This module provides simplified functions for executing code and managing
notebook workflows with sensible defaults and error handling.
"""

import asyncio
import logging
import time

from ..kernel.types import ExecutionResult
from ..notebook.session import NotebookSession
from ..notebook.types import NotebookCodeExecutionResult, NotebookMarkdownCellResult
from ..notebook.utils import to_nbformat_outputs
from .outputs import extract_outputs, format_output, has_error

logger = logging.getLogger(__name__)


def convert_to_notebook_result(
    result: ExecutionResult,
    cell_index: int,
    *,
    elapsed_seconds: float | None = None,
    format_outputs: bool = True,
) -> NotebookCodeExecutionResult:
    """
    Create a NotebookCodeExecutionResult from a basic ExecutionResult.

    Args:
        result: Basic ExecutionResult from kernel execution
        cell_index: Index of the executed cell
        elapsed_seconds: Execution time
        format_outputs: Whether to process outputs for readability

    Returns:
        NotebookCodeExecutionResult with enhanced fields
    """
    outputs = to_nbformat_outputs(result) or []

    formatted_output = format_output(outputs) if format_outputs else ""
    error_message = None

    # Extract error message if execution failed
    if has_error(outputs):
        error_outputs = [out for out in outputs if out.get("output_type") == "error"]
        if error_outputs:
            error_message = format_output(error_outputs)

    # Use utils extract_outputs for consistent text outputs
    text_outputs = extract_outputs(outputs)

    return NotebookCodeExecutionResult(
        status=result.status,
        execution_count=result.execution_count,
        cell_index=cell_index,
        stdout=result.stdout,
        stderr=result.stderr,
        outputs=outputs,
        text_outputs=text_outputs,
        formatted_output=formatted_output,
        error_message=error_message,
        elapsed_seconds=elapsed_seconds,
    )


async def execute_code(
    kernel_session, code: str, *, timeout: float | None = 120.0, format_outputs: bool = True
) -> NotebookCodeExecutionResult:
    """
    Execute code in a kernel session with automatic output processing.

    Note: Returns NotebookCodeExecutionResult for consistency, with cell_index = -1
    to indicate this was direct kernel execution (not in a notebook).

    Args:
        kernel_session: Kernel session to execute in
        code: Python code to execute
        timeout: Execution timeout in seconds
        format_outputs: Whether to format outputs into readable strings

    Returns:
        NotebookCodeExecutionResult with processed outputs and metadata

    Examples:
        kernel = create_kernel("local")
        result = await execute_code(kernel, "2 + 3")

        if result.status == "ok":
            print(f"Result: {result.formatted_output}")
        else:
            print(f"Error: {result.error_message}")
    """
    start_time = time.time()

    logger.debug(f"Executing code: {code[:100]}{'...' if len(code) > 100 else ''}")

    try:
        # Only start if not already alive
        if not await kernel_session.is_alive():
            await kernel_session.start()

        logger.debug("Executing code in kernel session")

        if timeout:
            result = await asyncio.wait_for(kernel_session.execute(code), timeout=timeout)
        else:
            result = await kernel_session.execute(code)

        elapsed = time.time() - start_time
        logger.debug(f"Code execution completed in {elapsed:.2f}s, status: {result.status}")
        logger.debug(
            f"Result type: {type(result)}, outputs: "
            f"{len(result.outputs) if hasattr(result, 'outputs') else 'No outputs attr'}"
        )

        # Convert to NotebookCodeExecutionResult with cell_index = -1 for direct execution
        return convert_to_notebook_result(
            result, cell_index=-1, elapsed_seconds=elapsed, format_outputs=format_outputs
        )

    except TimeoutError:
        elapsed = time.time() - start_time
        return NotebookCodeExecutionResult(
            status="error",
            execution_count=None,
            cell_index=-1,
            stdout="",
            stderr="",
            outputs=[],
            text_outputs=[],
            formatted_output="",
            error_message=f"Execution timed out after {timeout}s",
            elapsed_seconds=elapsed,
        )

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"Code execution failed: {type(e).__name__}: {e}")
        logger.error(f"Error occurred during: {code[:100]}...")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")

        return NotebookCodeExecutionResult(
            status="error",
            execution_count=None,
            cell_index=-1,
            stdout="",
            stderr=str(e),
            outputs=[],
            text_outputs=[],
            formatted_output="",
            error_message=f"{type(e).__name__}: {e}",
            elapsed_seconds=elapsed,
        )


async def invoke_code_cell(
    notebook_session: NotebookSession,
    code: str,
    *,
    timeout: float | None = 120.0,
    format_outputs: bool = True,
) -> NotebookCodeExecutionResult:
    """
    Invoke Python code in a new notebook cell - agent-friendly execution.

    Args:
        notebook_session: NotebookSession to execute in
        code: Python code to execute
        timeout: Execution timeout in seconds
        format_outputs: Whether to format outputs into readable strings

    Returns:
        NotebookCodeExecutionResult with cell index and processed outputs

    Examples:
        kernel = create_kernel("remote", base_url="http://localhost:8888")
        doc = create_notebook_transport("remote", "analysis.ipynb",
                                      base_url="http://localhost:8888")
        nb = NotebookSession(kernel=kernel, doc=doc)

        result = await invoke_code_cell(nb, "import pandas as pd")
        print(f"Cell {result.cell_index}: {result.formatted_output}")
    """
    start_time = time.time()

    try:
        cell_index, result = await notebook_session.append_and_run(code, timeout=timeout)

        elapsed = time.time() - start_time

        nb_result = convert_to_notebook_result(
            result, cell_index=cell_index, elapsed_seconds=elapsed, format_outputs=format_outputs
        )

        return nb_result

    except TimeoutError:
        elapsed = time.time() - start_time
        return NotebookCodeExecutionResult(
            status="error",
            execution_count=None,
            cell_index=-1,
            stdout="",
            stderr="",
            outputs=[],
            text_outputs=[],
            formatted_output="",
            error_message=f"Execution timed out after {timeout}s",
            elapsed_seconds=elapsed,
        )

    except Exception as e:
        elapsed = time.time() - start_time
        return NotebookCodeExecutionResult(
            status="error",
            execution_count=None,
            cell_index=-1,
            stdout="",
            stderr="",
            outputs=[],
            text_outputs=[],
            formatted_output="",
            error_message=f"{type(e).__name__}: {e}",
            elapsed_seconds=elapsed,
        )


async def invoke_markdown_cell(
    notebook_session: NotebookSession,
    markdown: str,
    *,
    index: int | None = None,
) -> NotebookMarkdownCellResult:
    """
    Invoke markdown content in a new notebook cell - agent-friendly documentation.

    Args:
        notebook_session: NotebookSession to add the cell to.
        markdown: Markdown text content for the cell.
        index: Optional position to insert the cell. If None, appends to end.

    Returns:
        NotebookMarkdownCellResult with status, cell_index, error_message, and elapsed_seconds.
    """
    import time

    start_time = time.time()
    try:
        cell_index = await notebook_session.run_markdown(markdown, index=index)
        elapsed = time.time() - start_time
        return NotebookMarkdownCellResult(
            status="ok", cell_index=cell_index, error_message=None, elapsed_seconds=elapsed
        )
    except Exception as e:
        elapsed = time.time() - start_time
        return NotebookMarkdownCellResult(
            status="error",
            cell_index=None,
            error_message=f"{type(e).__name__}: {e}",
            elapsed_seconds=elapsed,
        )


async def invoke_notebook_cells(
    notebook_session: NotebookSession,
    cells: list[dict[str, str]],
    *,
    timeout: float | None = 120.0,
    format_outputs: bool = True,
) -> list[NotebookCodeExecutionResult | NotebookMarkdownCellResult]:
    """
    Invoke multiple cells (code and/or markdown) in sequence - agent workflow execution.

    Returns NotebookCodeExecutionResult for code cells,
    NotebookMarkdownCellResult for markdown cells.
    """
    results = []
    for cell in cells:
        cell_type = cell.get("type", "code")
        content = cell.get("content", "")
        if not content:
            raise ValueError(f"Empty content in cell: {cell}")
        if cell_type == "markdown":
            result = await invoke_markdown_cell(notebook_session, content)
        elif cell_type == "code":
            result = await invoke_code_cell(
                notebook_session,
                content,
                timeout=timeout,
                format_outputs=format_outputs,
            )
        else:
            raise ValueError(f"Unsupported cell_type: {cell_type}. Must be 'code' or 'markdown'")
        results.append(result)
    return results


# Session information and variable utilities
async def get_session_info(session) -> dict[str, any]:
    """
    Get basic information about a kernel session.

    Args:
        session: Kernel session object

    Returns:
        Dictionary with session information

    Examples:
        info = await get_session_info(kernel)
        print(f"Kernel alive: {info['alive']}")
        print(f"Connection: {info['connection_info']}")
    """
    info = {
        "type": type(session).__name__,
        "alive": await session.is_alive(),
        "connection_info": "N/A",
    }

    # Add kernel manager info if available (local sessions)
    if hasattr(session, "kernel_manager") and session.kernel_manager:
        km = session.kernel_manager
        info["connection_info"] = getattr(km, "connection_file", "Local kernel")
        info["kernel_name"] = getattr(km, "kernel_name", "Unknown")

    return info


async def get_variables(session) -> list[str]:
    """
    Get list of variables in the kernel session.

    Args:
        session: Kernel session object

    Returns:
        List of variable names

    Examples:
        vars = await get_variables(kernel)
        print(f"Variables: {vars}")
    """
    from ..kernel.variables import VariableManager

    try:
        var_manager = VariableManager(session)
        return await var_manager.list()
    except Exception as e:
        logger.warning(f"Could not list variables: {e}")
        return []


async def get_variable_value(session, name: str) -> any:
    """
    Get the value of a specific variable from the kernel session.

    Args:
        session: Kernel session object
        name: Variable name to retrieve

    Returns:
        Variable value or None if not found

    Examples:
        value = await get_variable_value(kernel, "my_data")
        print(f"my_data = {value}")
    """
    from ..kernel.variables import VariableManager

    try:
        var_manager = VariableManager(session)
        return await var_manager.get(name)
    except Exception as e:
        logger.warning(f"Could not get variable '{name}': {e}")
        return None


async def invoke_existing_cell(
    notebook_session: NotebookSession,
    cell_index: int,
    code: str,
    *,
    timeout: float | None = 30.0,
    format_outputs: bool = True,
) -> NotebookCodeExecutionResult:
    """
    Invoke code in an existing cell at the specified index - agent cell updates.

    This function replaces the source of an existing cell and executes it,
    which is different from invoke_code_cell() that creates a new cell.

    Args:
        notebook_session: NotebookSession to execute in
        cell_index: Index of the existing cell to execute (0-based)
        code: Python code to replace the cell source and execute
        timeout: Execution timeout in seconds
        format_outputs: Whether to format outputs into readable strings

    Returns:
        NotebookCodeExecutionResult with cell index and processed outputs

    Examples:
        # Execute code in existing cell 2
        result = await invoke_existing_cell(nb, 2, "print('Updated cell!')")
        print(f"Cell {result.cell_index}: {result.formatted_output}")

        # vs creating a new cell:
        # result = await invoke_code_cell(nb, "print('New cell!')")
    """
    start_time = time.time()

    try:
        await notebook_session.start()

        if timeout:
            result = await asyncio.wait_for(
                notebook_session.run_at(cell_index, code), timeout=timeout
            )
        else:
            result = await notebook_session.run_at(cell_index, code)

        elapsed = time.time() - start_time

        return convert_to_notebook_result(
            result, cell_index=cell_index, elapsed_seconds=elapsed, format_outputs=format_outputs
        )

    except TimeoutError:
        elapsed = time.time() - start_time
        return NotebookCodeExecutionResult(
            status="error",
            execution_count=None,
            cell_index=cell_index,
            stdout="",
            stderr="",
            outputs=[],
            text_outputs=[],
            formatted_output="",
            error_message=f"Execution timed out after {timeout}s",
            elapsed_seconds=elapsed,
        )

    except Exception as e:
        elapsed = time.time() - start_time
        return NotebookCodeExecutionResult(
            status="error",
            execution_count=None,
            cell_index=cell_index,
            stdout="",
            stderr="",
            outputs=[],
            text_outputs=[],
            formatted_output="",
            error_message=f"{type(e).__name__}: {e}",
            elapsed_seconds=elapsed,
        )
