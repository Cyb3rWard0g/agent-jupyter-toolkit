"""Bounded, retry-safe DataFrame imports into a live Python kernel."""

from __future__ import annotations

import base64
import hashlib
import json
import keyword
from typing import Any

from ..kernel import Session as KernelSession

_MAX_IMPORT_BYTES = 512 * 1024
_RECEIPT_PREFIX = "_ajt_table_import_"

# The private exec namespace keeps decoded rows out of kernel globals. The only
# successful namespace mutation installs both the frame and its receipt.
_IMPORT = """
import base64
import json
import weakref

def apply():
    saved = namespace.get(key)
    if saved is not None:
        saved_fingerprint, saved_frame, receipt = saved
        if saved_fingerprint != fingerprint:
            return {"ok": False, "error_code": "operation_conflict"}
        if name not in namespace or namespace[name] is not saved_frame():
            return {"ok": False, "error_code": "table_unavailable"}
        return receipt

    if name in namespace:
        return {"ok": False, "error_code": "name_conflict"}

    try:
        from pandas import DataFrame
    except ImportError:
        return {"ok": False, "error_code": "pandas_required"}

    table = json.loads(base64.b64decode(payload))
    frame = DataFrame.from_records(table["rows"], columns=table["columns"])
    frame.attrs["notebook_import"] = table["metadata"]
    receipt = {
        "ok": True, "table": name, "row_count": len(frame),
        "column_count": len(table["columns"]),
    }
    namespace.update({name: frame, key: (fingerprint, weakref.ref(frame), receipt)})
    return receipt

print(json.dumps(apply()))
"""


async def import_table(
    kernel: KernelSession,
    notebook_key: str,
    operation_id: str,
    name: str,
    columns: list[str],
    rows: list[list[Any]],
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Execute an import and return its receipt, without persisting data in a cell."""
    if not operation_id.strip() or len(operation_id) > 256:
        return {"ok": False, "error_code": "invalid_operation_id"}
    if (
        len(name) > 128
        or not name.isascii()
        or not name.isidentifier()
        or keyword.iskeyword(name)
        or name.startswith(_RECEIPT_PREFIX)
    ):
        return {"ok": False, "error_code": "invalid_table_name"}
    if (
        len(columns) > 256
        or len(set(columns)) != len(columns)
        or any(not column or len(column) > 256 for column in columns)
        or len(rows) > 10_000
        or any(len(row) != len(columns) for row in rows)
        or (rows and not columns)
    ):
        return {"ok": False, "error_code": "invalid_table_shape"}
    try:
        body = json.dumps(
            {"columns": columns, "rows": rows, "metadata": metadata or {}},
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        return {"ok": False, "error_code": "invalid_table_data"}
    if len(body) > _MAX_IMPORT_BYTES:
        return {"ok": False, "error_code": "table_too_large"}

    operation = json.dumps([notebook_key, operation_id]).encode("utf-8")
    key = _RECEIPT_PREFIX + hashlib.sha256(operation).hexdigest()
    fingerprint = hashlib.sha256(name.encode("utf-8") + b"\0" + body).hexdigest()
    payload = base64.b64encode(body).decode("ascii")
    code = (
        f"exec({_IMPORT!r}, {{'namespace': globals(), 'key': {key!r}, "
        f"'fingerprint': {fingerprint!r}, 'name': {name!r}, 'payload': {payload!r}}})"
    )
    try:
        result = await kernel.execute(code, timeout=45, store_history=False, stop_on_error=False)
    except Exception:
        return {"ok": False, "error_code": "execution_unconfirmed"}
    if result.timed_out or result.outcome == "unknown":
        return {"ok": False, "error_code": "execution_unconfirmed"}
    if result.status != "ok":
        return {"ok": False, "error_code": "execution_failed"}
    try:
        receipt = json.loads(result.stdout)
    except (TypeError, ValueError):
        return {"ok": False, "error_code": "execution_unconfirmed"}
    expected = {"ok": True, "table": name, "row_count": len(rows), "column_count": len(columns)}
    if receipt == expected:
        return {**expected, "operation_id": operation_id}
    if isinstance(receipt, dict) and receipt.get("ok") is False:
        error_code = receipt.get("error_code")
        if error_code in (
            "operation_conflict",
            "table_unavailable",
            "name_conflict",
            "pandas_required",
        ):
            return {"ok": False, "error_code": error_code}
    return {"ok": False, "error_code": "execution_unconfirmed"}
