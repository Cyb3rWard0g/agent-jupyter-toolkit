"""
Serialization utilities for variable values using the extensible mimetypes system.

This module provides high-level functions for serializing and deserializing Python objects
to and from MIME bundles with metadata. It acts as a simplified interface to the underlying
mimetypes registry system.

Features:
    - Automatic MIME type selection based on object type
    - Metadata preservation for accurate deserialization
    - Support for pandas DataFrames, arrays, and other complex types
    - Extensible through the mimetypes handler registry

Example:
    ```python
    import pandas as pd
    from agent_jupyter_toolkit.kernel.serialization import serialize_value, deserialize_value

    # Serialize a DataFrame
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    bundle = serialize_value(df)

    # Deserialize back to DataFrame
    restored_df = deserialize_value(bundle["data"], bundle["metadata"])
    ```
"""

from . import mimetypes


def serialize_value(value):
    """
    Serialize a Python object to a MIME bundle with metadata.

    This function converts a Python object into a structured format suitable
    for transmission or storage, including type information needed for
    accurate deserialization.

    Args:
        value: Any Python object to serialize

    Returns:
        dict: A bundle containing:
            - "data": Dict mapping MIME types to serialized representations
            - "metadata": Dict with type information for deserialization

    Example:
        ```python
        bundle = serialize_value([1, 2, 3])
        # Returns: {"data": {"application/json": "[1, 2, 3]"},
        #           "metadata": {"application/json": {"type": ["builtins", "list"]}}}
        ```
    """
    data, metadata = mimetypes.serialize_object(value)
    return {"data": data, "metadata": metadata}


def deserialize_value(data, metadata):
    """
    Deserialize a Python object from a MIME bundle with metadata.

    This function reconstructs a Python object from its serialized representation,
    using the metadata to determine the correct deserialization method.

    Args:
        data: Dict mapping MIME types to serialized data
        metadata: Dict containing type information for deserialization

    Returns:
        The reconstructed Python object

    Raises:
        ValueError: If no suitable deserializer is found
        TypeError: If deserialization fails due to type mismatch

    Example:
        ```python
        data = {"application/json": "[1, 2, 3]"}
        metadata = {"application/json": {"type": ["builtins", "list"]}}
        obj = deserialize_value(data, metadata)
        # Returns: [1, 2, 3]
        ```
    """
    return mimetypes.deserialize_object(data, metadata)
