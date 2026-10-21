"""Optional notebook-native Azure Monitor Logs / Sentinel tools.

All source access occurs in the kernel. The MCP host needs no Azure identity.
Tool workflows are serialized per session; generic code tools and browser cells
remain independent writers. Each query retains its own uniquely named result so
metadata never depends on a mutable user-selected DataFrame variable.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import keyword
import math
import unicodedata
import uuid
from datetime import datetime
from typing import Any

from mcp.types import ToolAnnotations

from agent_jupyter_toolkit.utils import execute_code, invoke_code_cell
from mcp_jupyter_notebook._mcp import Context, FastMCP

from .notebook.common import code_result, get_session


def _identifier(value: str) -> str:
    if (
        not value.isidentifier()
        or keyword.iskeyword(value)
        or value.startswith("_adt_")
        or hasattr(builtins, value)
        or unicodedata.normalize("NFKC", value) != value
    ):
        raise ValueError("Use a non-builtin Python identifier outside the reserved _adt_ namespace")
    return value


def _lock(session: Any) -> asyncio.Lock:
    lock = getattr(session, "_azure_monitor_tool_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        session._azure_monitor_tool_lock = lock
    return lock


def _payload(stdout: str) -> dict[str, Any]:
    try:
        value = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}
    return value if isinstance(value, dict) else {}


def _timeout(value: float) -> None:
    if isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= 600:
        raise ValueError("timeout must be greater than zero and at most 600 seconds")


def register_azure_monitor_tools(mcp: FastMCP) -> None:
    """Register explicit-identity connection, visible query and close tools."""

    @mcp.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        )
    )
    async def azure_monitor_connect(
        ctx: Context,
        notebook_path: str | None = None,
        connection_name: str = "logs",
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Create/reuse a Logs client in the kernel from trusted AZURE_LOGS_* settings.

        Install agent-data-toolkit[azure-monitor] in the kernel environment first.
        No tokens or secrets are accepted as tool arguments. Creating the client
        does not prove Azure identity or workspace permissions; a query does.
        """
        _identifier(connection_name)
        _timeout(timeout)
        session = get_session(ctx, notebook_path)
        code = (
            "from agent_data_toolkit.azure_monitor import AzureMonitorLogsClient as _adt_client\n"
            f"if {connection_name!r} not in globals():\n"
            f"    {connection_name} = _adt_client.from_env()\n"
            f"elif not isinstance({connection_name}, _adt_client):\n"
            "    raise TypeError('Connection variable holds a different type')\n"
        )
        async with _lock(session):
            result = await execute_code(session.kernel, code, timeout=timeout)
        return {**code_result(result), "connection_name": connection_name, "source_verified": False}

    @mcp.tool(
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        )
    )
    async def azure_monitor_query_to_df(
        ctx: Context,
        query: str,
        start_time: str,
        end_time: str,
        notebook_path: str | None = None,
        connection_name: str = "logs",
        df_name: str | None = None,
        table: int = 0,
        allow_partial: bool = False,
        max_rows: int = 100_000,
        preview_rows: int = 5,
        server_timeout: int = 120,
        timeout: float = 150.0,
    ) -> dict[str, Any]:
        """Execute KQL in one visible cell and retain its result and DataFrame.

        start_time/end_time must be absolute timezone-aware ISO timestamps.
        Partial/client-truncated results are retained but DataFrame creation needs
        allow_partial=true. max_rows bounds retained rows, not server scan volume.
        An unknown execution outcome must be inspected before retrying the query.
        """
        _identifier(connection_name)
        resolved_df = _identifier(df_name or f"logs_df_{uuid.uuid4().hex[:12]}")
        if resolved_df == connection_name:
            raise ValueError("df_name must differ from connection_name")
        _timeout(timeout)
        # Host validation uses only Python, not the optional kernel-side connector.
        start, end = datetime.fromisoformat(start_time), datetime.fromisoformat(end_time)
        if start.utcoffset() is None or end.utcoffset() is None or start >= end:
            raise ValueError("Provide an ordered, timezone-aware start_time and end_time")
        if not query.strip() or len(query) > 65_536:
            raise ValueError("query must contain 1 to 65536 characters")
        for name, value, lower, upper in (
            ("table", table, 0, 1000),
            ("max_rows", max_rows, 1, 1_000_000),
            ("preview_rows", preview_rows, 0, 20),
            ("server_timeout", server_timeout, 1, 600),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
                raise ValueError(f"{name} must be an integer between {lower} and {upper}")
        record = f"_adt_logs_result_{uuid.uuid4().hex}"
        code = (
            "from datetime import datetime as _adt_datetime\n"
            "from agent_data_toolkit.azure_monitor import AzureMonitorLogsClient as _adt_client\n"
            f"if {connection_name!r} not in globals():\n"
            f"    {connection_name} = _adt_client.from_env()\n"
            f"elif not isinstance({connection_name}, _adt_client):\n"
            "    raise TypeError('Connection variable holds a different type')\n"
            f"{record} = {{'query_finished': False, 'dataframe_created': False}}\n"
            f"{record}['result'] = {connection_name}.query(\n"
            f"    {query!r},\n"
            f"    timespan=(_adt_datetime.fromisoformat({start.isoformat()!r}),\n"
            f"              _adt_datetime.fromisoformat({end.isoformat()!r})),\n"
            f"    max_rows={max_rows}, server_timeout={server_timeout})\n"
            f"{record}['query_finished'] = True\n"
            f"{resolved_df} = {record}['result'].to_dataframe(\n"
            f"    table={table}, allow_partial={allow_partial!r})\n"
            f"{record}['frame'] = {resolved_df}\n"
            f"{record}['dataframe_created'] = True\n"
            f"{resolved_df}.head({preview_rows})"
        )
        session = get_session(ctx, notebook_path)
        async with _lock(session):
            execution = await invoke_code_cell(session, code, timeout=timeout)
            output = {
                **code_result(execution),
                "df_name": resolved_df,
                "result_name": record,
                "metadata_extracted": False,
                "dataframe_created": None,
            }
            if execution.outcome == "unknown" or execution.timed_out:
                return output
            # Read the retained per-query object, never requery or inspect a name
            # another tool/browser cell may have rebound to a different dataset.
            metadata_code = (
                "import json as _adt_json\n"
                f"_adt_record = globals().get({record!r}, {{}})\n"
                "_adt_meta = {'query_finished': _adt_record.get('query_finished', False),\n"
                "    'dataframe_created': _adt_record.get('dataframe_created', False)}\n"
                "if 'result' in _adt_record:\n"
                "    _adt_meta['source'] = _adt_record['result'].metadata()\n"
                "if _adt_meta['dataframe_created']:\n"
                "    _adt_frame = _adt_record['frame']\n"
                f"    _adt_sample = _adt_frame.head({preview_rows}).to_json(\n"
                "        orient='records', date_format='iso')\n"
                "    _adt_meta['row_count'], _adt_meta['col_count'] = _adt_frame.shape\n"
                "    _adt_meta['preview_truncated'] = len(_adt_sample) > 32768\n"
                "    _adt_meta['sample'] = (None if _adt_meta['preview_truncated']\n"
                "        else _adt_json.loads(_adt_sample))\n"
                "print(_adt_json.dumps(_adt_meta))\n"
            )
            metadata = await execute_code(session.kernel, metadata_code, timeout=min(timeout, 30.0))
            payload = _payload(metadata.stdout) if metadata.status == "ok" else {}
            output.update(payload)
            output["metadata_extracted"] = bool(payload)
            output["metadata_execution"] = {
                "status": metadata.status,
                "outcome": metadata.outcome,
                "timed_out": metadata.timed_out,
            }
            output["ok"] = (
                execution.status == "ok"
                and execution.persistence_status == "ok"
                and bool(payload.get("dataframe_created"))
            )
            return output

    @mcp.tool(
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
        )
    )
    async def azure_monitor_close(
        ctx: Context,
        notebook_path: str | None = None,
        connection_name: str = "logs",
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Close and remove a kernel-side Logs client; keep notebook results and datasets."""
        _identifier(connection_name)
        _timeout(timeout)
        session = get_session(ctx, notebook_path)
        code = (
            "from agent_data_toolkit.azure_monitor import AzureMonitorLogsClient as _adt_client\n"
            f"if {connection_name!r} in globals():\n"
            f"    if not isinstance({connection_name}, _adt_client):\n"
            "        raise TypeError('Connection variable holds a different type')\n"
            f"    {connection_name}.close()\n"
            f"    del {connection_name}\n"
        )
        async with _lock(session):
            result = await execute_code(session.kernel, code, timeout=timeout)
        return {**code_result(result), "connection_name": connection_name}
