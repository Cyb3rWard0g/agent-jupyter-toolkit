"""
Flexible MIME type serialization/deserialization for kernel variable values.

This module provides a registry and utilities for serializing and deserializing Python objects
to and from MIME bundles, supporting efficient formats (Arrow, JSON), extensibility, and security.

Extension: Register new handlers for custom types using `register_handler`.
"""

import json
import logging
import pickle
from collections.abc import Callable
from typing import Any

# Handler registry: (module, class) -> list of (mimetype, serializer, deserializer)
MIMETYPE_HANDLERS: dict[tuple[str, str], list[tuple[str, Callable, Callable]]] = {}
log = logging.getLogger(__name__)


def register_handler(
    module: str,
    cls: str,
    mimetype: str,
    serializer: Callable,
    deserializer: Callable,
):
    """
    Register a custom handler for a type and mimetype.
    Args:
        module: Module name (e.g., 'pandas.core.frame')
        cls: Class name (e.g., 'DataFrame')
        mimetype: MIME type string (e.g., 'application/json')
        serializer: Function(obj) -> data
        deserializer: Function(data, mimetype) -> obj
    """
    key = (module, cls)
    if key not in MIMETYPE_HANDLERS:
        MIMETYPE_HANDLERS[key] = []
    MIMETYPE_HANDLERS[key].append((mimetype, serializer, deserializer))


def register_pandas_handlers():
    """Register pandas DataFrame handlers for Arrow and JSON serialization."""
    try:
        import pandas as pd
        import pyarrow as pa

        def serialize_df_arrow(obj):
            table = pa.Table.from_pandas(obj)
            sink = pa.BufferOutputStream()
            with pa.ipc.new_stream(sink, table.schema) as writer:
                writer.write_table(table)
            return sink.getvalue().to_pybytes()

        def deserialize_df_arrow(data, mimetype):
            buffer = pa.py_buffer(data)
            with pa.ipc.open_stream(buffer) as reader:
                return reader.read_pandas()

        register_handler(
            "pandas.core.frame",
            "DataFrame",
            "application/vnd.apache.arrow.stream",
            serialize_df_arrow,
            deserialize_df_arrow,
        )
        register_handler(
            "pandas.core.frame",
            "DataFrame",
            "application/json",
            lambda df: df.to_json(),
            lambda s, _: pd.read_json(s),
        )
    except Exception as exc:
        # Optional scientific stack may be missing or ABI-incompatible.
        log.debug("Skipping pandas MIME handlers: %s", exc)


def register_ndarray_handlers():
    """
    Register ndarray handler for JSON serialization.

    When NumPy is unavailable, values deserialize back to plain Python lists instead of ndarrays.
    """

    def serialize_numpy(obj):
        return obj.tolist()

    try:
        import numpy as np
    except Exception:

        def deserialize_numpy(data, mimetype):
            return data

    else:

        def deserialize_numpy(data, mimetype):
            return np.array(data)

    register_handler("numpy", "ndarray", "application/json", serialize_numpy, deserialize_numpy)


def get_type_key(obj: Any) -> tuple[str, str]:
    """
    Return (module, class) tuple for an object's type.
    """
    t = type(obj)
    return (getattr(t, "__module__", "*"), getattr(t, "__name__", "*"))


def serialize_object(obj: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Serialize an object to a MIME bundle and metadata.
    Tries registered handlers, then JSON, then pickle.
    Returns:
        (data: {mimetype: value}, metadata: {mimetype: {type: (module, class)}})
    """
    key = get_type_key(obj)
    handlers = MIMETYPE_HANDLERS.get(key, [])
    for mimetype, serializer, _ in handlers:
        try:
            data = serializer(obj)
            return {mimetype: data}, {mimetype: {"type": key}}
        except Exception:
            continue
    # Fallbacks
    try:
        return {"application/json": json.dumps(obj)}, {"application/json": {"type": key}}
    except Exception:
        pickled = pickle.dumps(obj)
        log.warning("Using pickle for serialization. Only use with trusted data.")
        return {"application/python-pickle": pickled.hex()}, {
            "application/python-pickle": {"type": key}
        }


def deserialize_object(data: dict[str, Any], metadata: dict[str, Any] | None = None) -> Any:
    """
    Deserialize an object from a MIME bundle and metadata.
    Tries registered handlers, then JSON, then pickle.
    Returns:
        The deserialized Python object.
    """
    metadata = metadata or {}
    for mimetype, value in data.items():
        typeinfo = metadata.get(mimetype, {}).get("type", (None, None))
        handlers = MIMETYPE_HANDLERS.get(typeinfo, [])
        for mt, _, deserializer in handlers:
            if mt == mimetype:
                return deserializer(value, mimetype)
        if mimetype == "application/json":
            return json.loads(value)
        if mimetype == "application/python-pickle":
            log.warning("Using pickle for deserialization. Only use with trusted data.")
            return pickle.loads(bytes.fromhex(value))
    raise ValueError("No valid deserialization data found")


# Register image (PIL) handlers
def register_image_handlers():
    """Register PIL.Image handler for PNG serialization (base64-encoded)."""
    try:
        import base64
        import io

        from PIL import Image

        def serialize_pil_image(obj):
            buf = io.BytesIO()
            obj.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("ascii")

        def deserialize_pil_image(data, mimetype):
            return Image.open(io.BytesIO(base64.b64decode(data)))

        register_handler(
            "PIL.Image",
            "Image",
            "image/png",
            serialize_pil_image,
            deserialize_pil_image,
        )
    except Exception as exc:
        log.debug("Skipping image MIME handlers: %s", exc)


# Register array.array handler
def register_array_handlers():
    """Register array.array handler for JSON serialization (as list)."""
    try:
        import array

        def serialize_array(obj):
            return obj.tolist()

        def deserialize_array(data, mimetype):
            # Default to 'd' (double) typecode; adjust as needed
            return array.array("d", data)

        register_handler("array", "array", "application/json", serialize_array, deserialize_array)
    except Exception as exc:
        log.debug("Skipping array MIME handlers: %s", exc)


register_pandas_handlers()
register_ndarray_handlers()
register_image_handlers()
register_array_handlers()
