"""
Output parsing and formatting utilities.

This module provides functions to extract and format execution outputs
from various nbformat output types into human-readable strings.
"""

from typing import Any


def extract_outputs(outputs: list[dict[str, Any]]) -> list[str]:
    """
    Extract human-readable text from nbformat-style outputs.

    Args:
        outputs: List of nbformat output dictionaries

    Returns:
        List of human-readable strings

    Examples:
        result = await execute_code(kernel, "2 + 3")
        text_outputs = extract_outputs(result.outputs)
        # → ['5']
    """
    extracted = []
    for output in outputs or []:
        output_type = output.get("output_type")

        if output_type in {"execute_result", "display_data"}:
            # Result values and display data
            data = output.get("data", {})
            text_plain = data.get("text/plain")
            if text_plain is not None:
                if isinstance(text_plain, list):
                    extracted.append("".join(text_plain))
                else:
                    extracted.append(str(text_plain))

        elif output_type == "stream" and output.get("name") == "stdout":
            # Standard output
            text = output.get("text", "")
            if isinstance(text, list):
                extracted.append("".join(text))
            else:
                extracted.append(str(text))

        elif output_type == "error":
            # Error tracebacks
            traceback = output.get("traceback", [])
            if isinstance(traceback, list):
                extracted.append("\n".join(traceback))
            else:
                extracted.append(str(traceback))

    return extracted


def format_output(
    outputs: list[dict[str, Any]], *, include_types: bool = False, max_length: int = 1000
) -> str:
    """
    Format outputs into a single readable string.

    Args:
        outputs: List of nbformat output dictionaries
        include_types: Whether to prefix each output with its type
        max_length: Maximum length of formatted string

    Returns:
        Single formatted string containing all outputs

    Examples:
        result = await execute_code(kernel, "print('hello'); 2+3")
        formatted = format_output(result.outputs)
        # → "hello\n5"

        formatted = format_output(result.outputs, include_types=True)
        # → "[stdout] hello\n[result] 5"
    """
    extracted = []

    for output in outputs or []:
        output_type = output.get("output_type")
        prefix = f"[{output_type}] " if include_types else ""

        if output_type in {"execute_result", "display_data"}:
            data = output.get("data", {})
            text_plain = data.get("text/plain")
            if text_plain is not None:
                content = "".join(text_plain) if isinstance(text_plain, list) else str(text_plain)
                extracted.append(f"{prefix}{content}")

        elif output_type == "stream":
            text = output.get("text", "")
            content = "".join(text) if isinstance(text, list) else str(text)
            # Strip trailing newlines for cleaner formatting
            content = content.rstrip("\n")
            if content:
                extracted.append(f"{prefix}{content}")

        elif output_type == "error":
            traceback = output.get("traceback", [])
            content = "\n".join(traceback) if isinstance(traceback, list) else str(traceback)
            extracted.append(f"{prefix}{content}")

    result = "\n".join(extracted)

    # Truncate if too long
    if len(result) > max_length:
        result = result[: max_length - 3] + "..."

    return result


def get_result_value(outputs: list[dict[str, Any]]) -> Any:
    """
    Extract the primary result value from execution outputs.

    Args:
        outputs: List of nbformat output dictionaries

    Returns:
        The primary result value, or None if no result found

    Examples:
        result = await execute_code(kernel, "2 + 3")
        value = get_result_value(result.outputs)
        # → "5" (as string)
    """
    for output in outputs or []:
        if output.get("output_type") == "execute_result":
            data = output.get("data", {})
            text_plain = data.get("text/plain")
            if text_plain is not None:
                return "".join(text_plain) if isinstance(text_plain, list) else str(text_plain)
    return None


def has_error(outputs: list[dict[str, Any]]) -> bool:
    """
    Check if outputs contain any errors.

    Args:
        outputs: List of nbformat output dictionaries

    Returns:
        True if any output is an error, False otherwise
    """
    return any(output.get("output_type") == "error" for output in outputs or [])
