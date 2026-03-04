"""PostgreSQL helper tools for the MCP Jupyter Notebook server.

These tools are *optional* and intended to speed up common workflows:
- install a PostgreSQL client into the notebook kernel
- create/close a connection object stored as a kernel variable

Design goals:
- keep server-side dependencies minimal (connections live in the notebook kernel)
- avoid string-injection by transferring parameters via VariableManager
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from agent_jupyter_toolkit.kernel.variables import VariableManager
from agent_jupyter_toolkit.utils import ensure_packages_with_report, execute_code, invoke_code_cell

log = logging.getLogger("mcp-jupyter.tools.postgresql")


def _get_manager(ctx: Context):
    return ctx.request_context.lifespan_context.manager


def _get_session(ctx: Context, notebook_path: str | None = None):
    return _get_manager(ctx).get(notebook_path)


def _parse_last_line_json(stdout: str | None) -> dict[str, Any] | None:
    if not stdout:
        return None
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    last = lines[-1]
    try:
        payload = json.loads(last)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _unique_df_name(prefix: str = "df") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


def _ok_from_payload_or_result(payload: dict[str, Any] | None, status: str | None) -> bool:
    if payload is not None and "ok" in payload:
        return bool(payload.get("ok"))
    return status == "ok"


async def _ensure_kernel_postgres_connection(
    session,
    *,
    connection_name: str,
    autocommit: bool = True,
    prefer_agent_data_toolkit: bool = True,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Ensure *connection_name* exists in kernel globals.

    This is a **lazy init** helper used by other tools:
    - If the variable already exists, it's a no-op.
    - If missing, it attempts to create a connection from the **kernel env**:
      ``PG_DSN`` → ``POSTGRES_DSN`` → ``DATABASE_URL``.

    It intentionally does **not** install packages. If psycopg isn't importable
    in the kernel, callers should guide users to run ``postgresql_connect``.
    """
    vm = VariableManager(session.kernel)
    await vm.set("_mcp_pg_connection_name", connection_name)
    await vm.set("_mcp_pg_autocommit", bool(autocommit))
    await vm.set("_mcp_pg_prefer_adt", bool(prefer_agent_data_toolkit))

    code = """
import json
import os

name = globals().get("_mcp_pg_connection_name", "pg_conn")
autocommit = bool(globals().get("_mcp_pg_autocommit", True))
prefer_adt = bool(globals().get("_mcp_pg_prefer_adt", True))

payload = {
    "ok": False,
    "connection_name": name,
    "created": None,
    "already_present": False,
    "dsn_source": None,
    "error": None,
}

def _dsn_from_env() -> str | None:
    if os.environ.get("PG_DSN"):
        payload["dsn_source"] = "PG_DSN"
        return os.environ.get("PG_DSN")
    if os.environ.get("POSTGRES_DSN"):
        payload["dsn_source"] = "POSTGRES_DSN"
        return os.environ.get("POSTGRES_DSN")
    if os.environ.get("DATABASE_URL"):
        payload["dsn_source"] = "DATABASE_URL"
        return os.environ.get("DATABASE_URL")
    return None

try:
    existing = globals().get(name)
    if existing is not None:
        payload["ok"] = True
        payload["already_present"] = True
        payload["created"] = type(existing).__name__
        print(json.dumps(payload, default=str))
    else:
        dsn = _dsn_from_env()
        if not dsn:
            raise RuntimeError(
                "No DSN set in kernel env. Set PG_DSN/POSTGRES_DSN/DATABASE_URL, "
                "or run postgresql_connect with explicit params."
            )

        obj = None
        if prefer_adt:
            try:
                from agent_data_toolkit.postgresql import (
                    ConnectionInfo,
                    ConnectionManager,
                    PostgresClient,
                )

                info = ConnectionInfo(dsn=dsn, connect_timeout=10)
                mgr = ConnectionManager(info)
                obj = PostgresClient(mgr)
                payload["created"] = "agent-data-toolkit.PostgresClient"
            except Exception:
                obj = None

        if obj is None:
            try:
                import psycopg
            except Exception as e:
                raise RuntimeError(
                    "psycopg is not importable in this kernel. "
                    "Run postgresql_connect first (it installs psycopg[binary]). "
                    f"import error: {type(e).__name__}: {e}"
                )

            conn = psycopg.connect(dsn)
            try:
                conn.autocommit = autocommit
            except Exception:
                pass
            obj = conn
            payload["created"] = "psycopg"

        globals()[name] = obj
        payload["ok"] = True
        print(json.dumps(payload, default=str))
except Exception as e:
    payload["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(payload, default=str))
""".strip()

    res = await execute_code(session.kernel, code, timeout=timeout)
    payload = _parse_last_line_json(res.stdout)
    return {
        "ok": _ok_from_payload_or_result(payload, res.status),
        "parsed": payload,
        "stdout": res.stdout,
        "stderr": res.stderr,
    }


def register_postgresql_tools(mcp: FastMCP) -> None:
    """Register optional PostgreSQL helper tools."""

    @mcp.tool(
        title="PostgreSQL Connect",
        annotations=ToolAnnotations(
            title="PostgreSQL Connect",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    async def postgresql_connect(
        ctx: Context,
        notebook_path: str | None = None,
        connection_name: str = "pg_conn",
        dsn: str | None = None,
        prefer_agent_data_toolkit: bool = True,
        use_mcp_env_dsn: bool = False,
        require_ssl: bool = False,
        default_sslmode: str = "require",
        host: str | None = None,
        port: int = 5432,
        database: str | None = None,
        user: str | None = None,
        password: str | None = None,
        sslmode: str | None = None,
        autocommit: bool = True,
        connect_timeout_seconds: int | None = 10,
        test_query: str | None = "SELECT 1",
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Create a PostgreSQL client/connection in the notebook kernel.

        By default, connection details are resolved from the *kernel environment*:

        - ``PG_DSN``
        - ``POSTGRES_DSN``
        - ``DATABASE_URL``

        If those are not set, it falls back to individual libpq env vars
        (``PGHOST``, ``PGPORT``, ``PGDATABASE``, ``PGUSER``, ``PGPASSWORD``, ``PGSSLMODE``)
        and/or the explicit parameters passed to this tool.

        When ``prefer_agent_data_toolkit=True`` and ``agent-data-toolkit`` is
        importable in the kernel, this tool will create an
        ``agent_data_toolkit.postgresql.PostgresClient`` and store it in
        the kernel as ``connection_name``.

        Otherwise, it stores a raw ``psycopg.Connection`` object.
        """
        session = _get_session(ctx, notebook_path)
        await ctx.info("Preparing PostgreSQL connection in kernel")

        # ── Step 0: Ensure required packages ──────────────────────────
        packages = ["psycopg[binary]"]
        if prefer_agent_data_toolkit:
            packages.append("agent-data-toolkit[postgresql]")

        pkg_report = await ensure_packages_with_report(session.kernel, packages)
        if not pkg_report.get("success", False):
            rep = pkg_report.get("report", {}) or {}
            psy_ok = bool(rep.get("psycopg[binary]", {}).get("success", False))
            if not psy_ok:
                return {
                    "ok": False,
                    "error": "Failed to install psycopg[binary] in the kernel",
                    "package_report": rep,
                }

        # ── Step 1: Transfer connection parameters via VariableManager ─
        # If DSN wasn't provided explicitly, optionally fall back to the
        # MCP server process environment. This is important in "server"
        # mode because the remote kernel won't inherit local env vars.
        if dsn is None and use_mcp_env_dsn:
            dsn = os.getenv("PG_DSN") or os.getenv("POSTGRES_DSN") or os.getenv("DATABASE_URL")

        params: dict[str, Any] = {
            "dsn": dsn,
            "require_ssl": require_ssl,
            "default_sslmode": default_sslmode,
            "host": host,
            "port": port,
            "database": database,
            "user": user,
            "password": password,
            "sslmode": sslmode,
            "connect_timeout": connect_timeout_seconds,
            "prefer_agent_data_toolkit": prefer_agent_data_toolkit,
        }
        params = {k: v for k, v in params.items() if v is not None}

        vm = VariableManager(session.kernel)
        await vm.set("_mcp_pg_params", params)
        await vm.set("_mcp_pg_autocommit", autocommit)
        await vm.set("_mcp_pg_connection_name", connection_name)
        await vm.set("_mcp_pg_test_query", test_query)

        # ── Step 2: HIDDEN — create connection in the kernel ──────────
        # All the heavy lifting (DSN resolution, SSL, psycopg import,
        # PostgresClient wrapping, test query) happens here. The result
        # is a JSON status line printed to stdout.
        hidden_code = """
import json
import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def _with_query_param(dsn: str, key: str, value: str | None) -> str:
    try:
        u = urlparse(dsn)
        if not u.scheme:
            return dsn
        q = dict(parse_qsl(u.query))
        if value is None:
            q.pop(key, None)
        else:
            q[key] = value
        return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(q), u.fragment))
    except Exception:
        return dsn


def _ensure_sslmode(dsn: str, default: str, *, enforce_minimum: bool) -> str:
    try:
        u = urlparse(dsn)
        if not u.scheme:
            return dsn
        q = dict(parse_qsl(u.query))
        if "sslmode" not in q:
            return _with_query_param(dsn, "sslmode", default)
        if enforce_minimum and str(q.get("sslmode", "")).lower() in {"disable", "allow", "prefer"}:
            return _with_query_param(dsn, "sslmode", default)
        return dsn
    except Exception:
        return dsn


def _redact_dsn(dsn: str) -> str:
    try:
        u = urlparse(dsn)
        if not u.scheme:
            return "dsn=<raw>"
        user = u.username or ""
        host = u.hostname or ""
        db = (u.path or "").lstrip("/")
        q = dict(parse_qsl(u.query))
        return (
            f"driver={u.scheme} user={user} host={host} "
            f"port={u.port} db={db} sslmode={q.get('sslmode')!r}"
        )
    except Exception:
        return "dsn=<unparsed>"

try:
    import psycopg
except Exception as e:
    print(json.dumps({"ok": False, "error": f"import psycopg failed: {type(e).__name__}: {e}"}))
    raise

_params = dict(globals().get("_mcp_pg_params", {}) or {})
_explicit_dsn = _params.get("dsn")
_require_ssl = bool(_params.get("require_ssl", False))
_default_sslmode = str(_params.get("default_sslmode", "require"))
_prefer_adt = bool(_params.get("prefer_agent_data_toolkit", True))

if "database" in _params and "dbname" not in _params:
    _params["dbname"] = _params.pop("database")

_conn_name = globals().get("_mcp_pg_connection_name", "pg_conn")
_autocommit = bool(globals().get("_mcp_pg_autocommit", False))
_test_query = globals().get("_mcp_pg_test_query", "SELECT 1")


def _dsn_from_env() -> str:
    dsn = (
        os.environ.get("PG_DSN")
        or os.environ.get("POSTGRES_DSN")
        or os.environ.get("DATABASE_URL")
    )
    if not dsn:
        return ""
    if _require_ssl:
        return _ensure_sslmode(dsn, _default_sslmode, enforce_minimum=_require_ssl)
    return dsn


def _fallback_conninfo_params() -> dict:
    out = {}
    if _params.get("host") is not None:
        out["host"] = _params.get("host")
    elif os.environ.get("PGHOST"):
        out["host"] = os.environ.get("PGHOST")

    if _params.get("port") is not None:
        out["port"] = _params.get("port")
    elif os.environ.get("PGPORT"):
        try:
            out["port"] = int(os.environ.get("PGPORT"))
        except Exception:
            pass

    if _params.get("dbname") is not None:
        out["dbname"] = _params.get("dbname")
    elif os.environ.get("PGDATABASE"):
        out["dbname"] = os.environ.get("PGDATABASE")

    if _params.get("user") is not None:
        out["user"] = _params.get("user")
    elif os.environ.get("PGUSER"):
        out["user"] = os.environ.get("PGUSER")

    if _params.get("password") is not None:
        out["password"] = _params.get("password")
    elif os.environ.get("PGPASSWORD"):
        out["password"] = os.environ.get("PGPASSWORD")

    sslmode = _params.get("sslmode") or os.environ.get("PGSSLMODE")
    if sslmode:
        out["sslmode"] = sslmode
    return out


def _connect_psycopg() -> tuple[object, str, str]:
    dsn_used = None
    if _explicit_dsn:
        dsn_used = _explicit_dsn
        if _require_ssl:
            dsn_used = _ensure_sslmode(dsn_used, _default_sslmode, enforce_minimum=True)
    else:
        dsn_used = _dsn_from_env() or None

    if dsn_used:
        conn = psycopg.connect(
            dsn_used,
            **{k: v for k, v in _params.items() if k in {"connect_timeout"}},
        )
        return conn, "dsn", _redact_dsn(dsn_used)

    kw = _fallback_conninfo_params()
    if not kw:
        raise RuntimeError(
            "No DSN found. Set PG_DSN/POSTGRES_DSN/DATABASE_URL in the kernel environment, "
            "or provide connection params (host/user/password/database)."
        )
    conn = psycopg.connect(**{**kw, **{k: v for k, v in _params.items() if k == "connect_timeout"}})
    return conn, "params", "dsn=<params>"

try:
    created_type = "psycopg"
    dsn_mode = "unknown"
    dsn_redacted = None

    _conn, dsn_mode, dsn_redacted = _connect_psycopg()
    try:
        _conn.autocommit = _autocommit
    except Exception:
        pass

    obj = _conn

    if _prefer_adt:
        try:
            from agent_data_toolkit.postgresql import (
                ConnectionInfo,
                ConnectionManager,
                PostgresClient,
            )

            if _explicit_dsn or _dsn_from_env():
                dsn_for_client = _explicit_dsn or _dsn_from_env()
                info = ConnectionInfo(
                    dsn=dsn_for_client,
                    connect_timeout=int(_params.get("connect_timeout", 10)),
                )
                mgr = ConnectionManager(info)
                obj = PostgresClient(mgr)
                created_type = "agent-data-toolkit.PostgresClient"
        except Exception:
            pass

    globals()[_conn_name] = obj

    test_result = None
    if _test_query and created_type == "psycopg":
        try:
            with _conn.cursor() as cur:
                cur.execute(_test_query)
                row = cur.fetchone() if cur.description else None
                test_result = row
        except Exception as e:
            test_result = {"error": f"test_query failed: {type(e).__name__}: {e}"}

    print(
        json.dumps(
            {
                "ok": True,
                "connection_name": _conn_name,
                "created": created_type,
                "dsn_mode": dsn_mode,
                "dsn": dsn_redacted,
                "test_result": test_result,
            },
            default=str,
        )
    )
except Exception as e:
    print(json.dumps({"ok": False, "error": f"connect failed: {type(e).__name__}: {e}"}))
""".strip()

        hidden_res = await execute_code(session.kernel, hidden_code, timeout=timeout)
        hidden_payload = _parse_last_line_json(hidden_res.stdout)

        if not _ok_from_payload_or_result(hidden_payload, hidden_res.status):
            error_msg = (
                (hidden_payload or {}).get("error")
                or hidden_res.stderr
                or "Connection failed (unknown error)"
            )
            return {
                "ok": False,
                "connection_name": connection_name,
                "error": error_msg,
                "package_report": pkg_report.get("report", {}),
                "parsed": hidden_payload,
            }

        # ── Step 3: VISIBLE — clean summary cell ─────────────────────
        # The connection already lives in globals(). This cell just
        # confirms its type and prints a redacted summary for the user.
        created_type = (hidden_payload or {}).get("created", "unknown")
        dsn_info = (hidden_payload or {}).get("dsn", "")

        visible_code = (
            f"# PostgreSQL connection\n"
            f'print(f"{{type({connection_name}).__name__}} ready'
            f' \u2014 {dsn_info}")'
        )

        vis_res = await invoke_code_cell(session, visible_code, timeout=30.0)

        return {
            "ok": True,
            "connection_name": connection_name,
            "created": created_type,
            "dsn_mode": (hidden_payload or {}).get("dsn_mode"),
            "dsn": dsn_info,
            "test_result": (hidden_payload or {}).get("test_result"),
            "package_report": pkg_report.get("report", {}),
            "cell_index": vis_res.cell_index,
        }

    @mcp.tool(
        title="PostgreSQL Test Connection",
        annotations=ToolAnnotations(
            title="PostgreSQL Test Connection",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    async def postgresql_test_connection(
        ctx: Context,
        notebook_path: str | None = None,
        connection_name: str = "pg_conn",
        test_query: str = "SELECT 1",
        auto_connect_from_env: bool = True,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Validate that a kernel connection exists and can run a query.

        If ``auto_connect_from_env=True`` and ``connection_name`` is missing,
        attempts to create it from kernel env vars (``PG_DSN`` / ``POSTGRES_DSN`` /
        ``DATABASE_URL``). This does **not** install packages.
        """
        session = _get_session(ctx, notebook_path)
        await ctx.info(f"Testing PostgreSQL connection variable: {connection_name}")

        if auto_connect_from_env:
            ensured = await _ensure_kernel_postgres_connection(
                session,
                connection_name=connection_name,
                autocommit=True,
                prefer_agent_data_toolkit=True,
                timeout=min(timeout, 30.0),
            )
            if not ensured.get("ok", False):
                return {
                    "ok": False,
                    "connection_name": connection_name,
                    "error": ((ensured.get("parsed") or {}).get("error") or "ensure failed"),
                    "ensure": ensured,
                }

        vm = VariableManager(session.kernel)
        await vm.set("_mcp_pg_connection_name", connection_name)
        await vm.set("_mcp_pg_test_query", test_query)

        code = """
import json

conn_name = globals().get("_mcp_pg_connection_name", "pg_conn")
sql = globals().get("_mcp_pg_test_query", "SELECT 1")
obj = globals().get(conn_name)

payload = {"ok": False, "connection_name": conn_name, "result": None, "error": None}

try:
    if obj is None:
        raise RuntimeError(
            f"Connection variable {conn_name!r} not found. "
            "Run postgresql_connect first, or enable "
            "auto_connect_from_env."
        )

    if hasattr(obj, "query_rows"):
        rows = obj.query_rows(sql=sql, params=None)
        payload["result"] = rows[:1] if isinstance(rows, list) else rows
    else:
        with obj.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone() if cur.description else None
            payload["result"] = row

    payload["ok"] = True
    print(json.dumps(payload, default=str))
except Exception as e:
    payload["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(payload, default=str))
""".strip()

        res = await execute_code(session.kernel, code, timeout=timeout)
        payload = _parse_last_line_json(res.stdout)
        return {
            "ok": _ok_from_payload_or_result(payload, res.status),
            "connection_name": connection_name,
            "result": (payload or {}).get("result"),
            "error": (payload or {}).get("error"),
            "stdout": res.stdout,
            "stderr": res.stderr,
        }

    @mcp.tool(
        title="PostgreSQL Query to DataFrame",
        annotations=ToolAnnotations(
            title="PostgreSQL Query to DataFrame",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    async def postgresql_query_to_df(
        ctx: Context,
        raw_sql: str,
        notebook_path: str | None = None,
        connection_name: str = "pg_conn",
        df_name: str | None = None,
        limit: int | None = None,
        preview_rows: int = 5,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Execute a SQL query and create a Pandas DataFrame in the notebook.

        This tool assumes you've already created a kernel connection/client
        via :func:`postgresql_connect` and stored it in ``connection_name``.

        Behavior
        --------
        1. Inserts a **visible** code cell that runs the SQL query, assigns
           the resulting DataFrame to ``df_name``, and displays a preview
           (``head(preview_rows)``).  This is the **only** Postgres round-trip.
        2. Runs a **hidden** kernel execution that introspects the DataFrame
           already living in ``globals()`` to extract structured metadata
           (schema, row/col counts, JSON sample) — **no second query**.

        The metadata returned to the caller describes the *exact* DataFrame
        the user sees and the agent can reference in subsequent code cells.

        Connection object support
        -------------------------
        - If ``connection_name`` is an ``agent_data_toolkit.postgresql.PostgresClient``,
          the visible cell calls its ``query_df`` method.
        - Otherwise falls back to ``pandas.read_sql_query`` with a DBAPI connection.
        """
        session = _get_session(ctx, notebook_path)
        await ctx.info("Executing PostgreSQL query into a DataFrame")

        # Ensure common data deps exist in the kernel.
        # agent-data-toolkit[postgresql] intentionally includes pandas/pyarrow.
        pkg_report = await ensure_packages_with_report(
            session.kernel,
            ["agent-data-toolkit[postgresql]"],
        )

        # Lazy init: if the connection variable doesn't exist yet, try to create it
        # from kernel env vars. This makes query_to_df usable as the first Postgres tool.
        ensured = await _ensure_kernel_postgres_connection(
            session,
            connection_name=connection_name,
            autocommit=True,
            prefer_agent_data_toolkit=True,
            timeout=min(timeout, 30.0),
        )
        if not ensured.get("ok", False):
            return {
                "ok": False,
                "error": ((ensured.get("parsed") or {}).get("error") or "ensure connection failed"),
                "connection_name": connection_name,
                "ensure": ensured,
                "package_report": pkg_report.get("report", {}),
            }

        resolved_df_name = df_name or _unique_df_name("df")
        if not resolved_df_name.isidentifier():
            return {
                "ok": False,
                "error": f"df_name must be a valid Python identifier: {resolved_df_name!r}",
            }

        # ── Step 1: Insert a VISIBLE cell that runs the query ────────
        # This is the ONLY Postgres round-trip.  The user sees a clean,
        # readable cell with the real PostgresClient/DBAPI call — something
        # they can copy, modify, and re-run independently.

        sql_literal = json.dumps(raw_sql)
        limit_repr = repr(limit)
        clean_code = (
            f'pg = globals().get("{connection_name}")\n'
            f"_sql = {sql_literal}\n"
            f"\n"
            f"if hasattr(pg, 'query_df'):\n"
            f"    {resolved_df_name} = pg.query_df(sql=_sql, limit={limit_repr})\n"
            f"else:\n"
            f"    import pandas as pd\n"
        )
        # For the raw psycopg fallback, build the limit-wrapped SQL inline.
        if limit is not None:
            clean_code += (
                f"    {resolved_df_name} = pd.read_sql_query("
                f'f"SELECT * FROM ({{_sql}}) AS _mcp_subq LIMIT {int(limit)}", pg)\n'
            )
        else:
            clean_code += f"    {resolved_df_name} = pd.read_sql_query(_sql, pg)\n"
        clean_code += f"{resolved_df_name}.head({int(preview_rows)})"

        visible_res = await invoke_code_cell(session, clean_code, timeout=timeout)

        if visible_res.status != "ok":
            return {
                "ok": False,
                "df_name": resolved_df_name,
                "cell_index": visible_res.cell_index,
                "error": (
                    visible_res.error_message
                    or (visible_res.stderr.strip() if visible_res.stderr else None)
                    or "query execution failed"
                ),
                "stdout": visible_res.stdout,
                "stderr": visible_res.stderr,
                "package_report": pkg_report.get("report", {}),
            }

        # ── Step 2: Extract metadata HIDDEN (no Postgres query) ──────
        # The DataFrame already lives in kernel globals from step 1.
        # We only introspect it here to return structured metadata
        # (schema, row/col counts, JSON sample) back to the LLM.
        # This guarantees the metadata describes the *exact* DataFrame
        # the user sees and the agent can reference in later cells.

        vm = VariableManager(session.kernel)
        await vm.set("_mcp_pg_df_name", resolved_df_name)
        await vm.set("_mcp_pg_preview_rows", int(preview_rows))

        metadata_code = """
import json
from datetime import date, datetime
from decimal import Decimal
import uuid

try:
    import numpy as _np
except Exception:
    _np = None

df_name = globals().get("_mcp_pg_df_name")
preview_rows = int(globals().get("_mcp_pg_preview_rows", 5) or 5)

payload = {
    "ok": False,
    "error": None,
    "schema": None,
    "row_count": None,
    "col_count": None,
    "sample": None,
}

def _json_default(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, (uuid.UUID, Decimal)):
        return str(obj)
    if _np is not None:
        if isinstance(obj, (_np.integer,)):
            return int(obj)
        if isinstance(obj, (_np.floating,)):
            return float(obj)
        if isinstance(obj, (_np.bool_,)):
            return bool(obj)
    if hasattr(obj, "tolist"):
        try:
            return obj.tolist()
        except Exception:
            pass
    return str(obj)

try:
    if df_name not in globals():
        raise RuntimeError(
            f"DataFrame '{df_name}' not found in kernel globals after query cell execution."
        )

    df = globals()[df_name]
    payload["schema"] = [(c, str(t)) for c, t in df.dtypes.items()]
    payload["row_count"] = int(df.shape[0])
    payload["col_count"] = int(df.shape[1])
    sample_json = df.head(preview_rows).to_json(orient="records", date_format="iso")
    payload["sample"] = json.loads(sample_json)
    payload["ok"] = True
    print(json.dumps(payload, default=_json_default))
except Exception as e:
    payload["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(payload, default=_json_default))
""".strip()

        meta_res = await execute_code(session.kernel, metadata_code, timeout=min(timeout, 30.0))
        meta_payload = _parse_last_line_json(meta_res.stdout)

        # Even if metadata extraction failed, the query itself succeeded
        # and the DataFrame exists in the notebook.  Report partial success
        # so the agent knows the variable is usable.
        meta_ok = _ok_from_payload_or_result(meta_payload, meta_res.status)

        return {
            "ok": True,
            "df_name": resolved_df_name,
            "cell_index": visible_res.cell_index,
            "schema": (meta_payload or {}).get("schema"),
            "row_count": (meta_payload or {}).get("row_count"),
            "col_count": (meta_payload or {}).get("col_count"),
            "limit": limit,
            "limit_applied": limit is not None,
            "sample": (meta_payload or {}).get("sample"),
            "error": None if meta_ok else (meta_payload or {}).get("error"),
            "metadata_extracted": meta_ok,
        }

    @mcp.tool(
        title="PostgreSQL Schema: List Tables",
        annotations=ToolAnnotations(
            title="PostgreSQL Schema: List Tables",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    async def postgresql_schema_list_tables(
        ctx: Context,
        notebook_path: str | None = None,
        connection_name: str = "pg_conn",
        schema_name: str | None = None,
        include_matviews: bool = False,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """List tables/views (and optionally materialized views).

        Returns a list of rows with keys: ``table_schema``, ``table_name``, ``table_type``.
        """
        session = _get_session(ctx, notebook_path)
        await ctx.info("Listing PostgreSQL tables")

        vm = VariableManager(session.kernel)
        await vm.set("_mcp_pg_connection_name", connection_name)
        await vm.set("_mcp_pg_schema_name", schema_name)
        await vm.set("_mcp_pg_include_matviews", bool(include_matviews))

        ensured = await _ensure_kernel_postgres_connection(
            session,
            connection_name=connection_name,
            autocommit=True,
            prefer_agent_data_toolkit=True,
            timeout=min(timeout, 30.0),
        )
        if not ensured.get("ok", False):
            return {
                "ok": False,
                "tables": [],
                "error": ((ensured.get("parsed") or {}).get("error") or "ensure connection failed"),
                "ensure": ensured,
            }

        code = """
import json

conn_name = globals().get("_mcp_pg_connection_name", "pg_conn")
schema = globals().get("_mcp_pg_schema_name")
include_matviews = bool(globals().get("_mcp_pg_include_matviews", False))

obj = globals().get(conn_name)
payload = {"ok": False, "tables": [], "error": None}

def _query_rows(sql: str, params: dict | None = None):
    if hasattr(obj, "query_rows"):
        return obj.query_rows(sql=sql, params=params)
    # psycopg path
    with obj.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in (cur.description or [])]
        rows = cur.fetchall() if cur.description else []
        return [dict(zip(cols, r)) for r in rows]

try:
    if obj is None:
        raise RuntimeError(
            f"Connection variable {conn_name!r} not found. "
            "Run postgresql_connect first."
        )

    if schema:
        sql_tv = '''
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_type IN ('BASE TABLE','VIEW') AND table_schema = %(schema)s
        ORDER BY table_schema, table_name
        '''
        rows = _query_rows(sql_tv, {"schema": schema})
    else:
        sql_tv = '''
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_type IN ('BASE TABLE','VIEW')
        ORDER BY table_schema, table_name
        '''
        rows = _query_rows(sql_tv)

    if include_matviews:
        if schema:
            sql_mv = '''
            SELECT schemaname AS table_schema,
                   matviewname AS table_name,
                   'MATERIALIZED VIEW' AS table_type
            FROM pg_matviews
            WHERE schemaname = %(schema)s
            ORDER BY schemaname, matviewname
            '''
            rows.extend(_query_rows(sql_mv, {"schema": schema}))
        else:
            sql_mv = '''
            SELECT schemaname AS table_schema,
                   matviewname AS table_name,
                   'MATERIALIZED VIEW' AS table_type
            FROM pg_matviews
            ORDER BY schemaname, matviewname
            '''
            rows.extend(_query_rows(sql_mv))

    payload["ok"] = True
    payload["tables"] = rows
    print(json.dumps(payload, default=str))
except Exception as e:
    payload["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(payload, default=str))
""".strip()

        res = await execute_code(session.kernel, code, timeout=timeout)
        payload = _parse_last_line_json(res.stdout)
        return {
            "ok": _ok_from_payload_or_result(payload, res.status),
            "tables": (payload or {}).get("tables", []),
            "error": (payload or {}).get("error"),
            "stdout": res.stdout,
            "stderr": res.stderr,
        }

    @mcp.tool(
        title="PostgreSQL Schema: List Columns",
        annotations=ToolAnnotations(
            title="PostgreSQL Schema: List Columns",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    async def postgresql_schema_list_columns(
        ctx: Context,
        schema_name: str,
        table: str,
        notebook_path: str | None = None,
        connection_name: str = "pg_conn",
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """List column metadata for a given (schema, table)."""
        session = _get_session(ctx, notebook_path)
        await ctx.info("Listing PostgreSQL columns")

        vm = VariableManager(session.kernel)
        await vm.set("_mcp_pg_connection_name", connection_name)
        await vm.set("_mcp_pg_schema_name", schema_name)
        await vm.set("_mcp_pg_table", table)

        ensured = await _ensure_kernel_postgres_connection(
            session,
            connection_name=connection_name,
            autocommit=True,
            prefer_agent_data_toolkit=True,
            timeout=min(timeout, 30.0),
        )
        if not ensured.get("ok", False):
            return {
                "ok": False,
                "columns": [],
                "error": ((ensured.get("parsed") or {}).get("error") or "ensure connection failed"),
                "ensure": ensured,
            }

        code = """
import json

conn_name = globals().get("_mcp_pg_connection_name", "pg_conn")
schema = globals().get("_mcp_pg_schema_name")
table = globals().get("_mcp_pg_table")

obj = globals().get(conn_name)
payload = {"ok": False, "columns": [], "error": None}

def _query_rows(sql: str, params: dict | None = None):
    if hasattr(obj, "query_rows"):
        return obj.query_rows(sql=sql, params=params)
    with obj.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in (cur.description or [])]
        rows = cur.fetchall() if cur.description else []
        return [dict(zip(cols, r)) for r in rows]

try:
    if obj is None:
        raise RuntimeError(
            f"Connection variable {conn_name!r} not found. "
            "Run postgresql_connect first."
        )

    sql = '''
    SELECT column_name,
           data_type,
           is_nullable,
           column_default,
           ordinal_position
    FROM information_schema.columns
    WHERE table_schema = %(schema)s AND table_name = %(table)s
    ORDER BY ordinal_position
    '''
    rows = _query_rows(sql, {"schema": schema, "table": table})
    payload["ok"] = True
    payload["columns"] = rows
    print(json.dumps(payload, default=str))
except Exception as e:
    payload["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(payload, default=str))
""".strip()

        res = await execute_code(session.kernel, code, timeout=timeout)
        payload = _parse_last_line_json(res.stdout)
        return {
            "ok": _ok_from_payload_or_result(payload, res.status),
            "columns": (payload or {}).get("columns", []),
            "error": (payload or {}).get("error"),
            "stdout": res.stdout,
            "stderr": res.stderr,
        }

    @mcp.tool(
        title="PostgreSQL Schema: Tree",
        annotations=ToolAnnotations(
            title="PostgreSQL Schema: Tree",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    async def postgresql_schema_tree(
        ctx: Context,
        notebook_path: str | None = None,
        connection_name: str = "pg_conn",
        limit_per_schema: int | None = 100,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Return a compact schema→tables mapping."""
        session = _get_session(ctx, notebook_path)
        await ctx.info("Building PostgreSQL schema tree")

        vm = VariableManager(session.kernel)
        await vm.set("_mcp_pg_connection_name", connection_name)
        await vm.set("_mcp_pg_limit_per_schema", limit_per_schema)

        ensured = await _ensure_kernel_postgres_connection(
            session,
            connection_name=connection_name,
            autocommit=True,
            prefer_agent_data_toolkit=True,
            timeout=min(timeout, 30.0),
        )
        if not ensured.get("ok", False):
            return {
                "ok": False,
                "schemas": [],
                "error": ((ensured.get("parsed") or {}).get("error") or "ensure connection failed"),
                "ensure": ensured,
            }

        code = """
import json

conn_name = globals().get("_mcp_pg_connection_name", "pg_conn")
limit_per_schema = globals().get("_mcp_pg_limit_per_schema", 100)
obj = globals().get(conn_name)

payload = {"ok": False, "schemas": [], "error": None}

def _query_rows(sql: str, params: dict | None = None):
    if hasattr(obj, "query_rows"):
        return obj.query_rows(sql=sql, params=params)
    with obj.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in (cur.description or [])]
        rows = cur.fetchall() if cur.description else []
        return [dict(zip(cols, r)) for r in rows]

try:
    if obj is None:
        raise RuntimeError(
            f"Connection variable {conn_name!r} not found. "
            "Run postgresql_connect first."
        )

    sql_s = (
        "SELECT schema_name AS name "
        "FROM information_schema.schemata "
        "ORDER BY schema_name"
    )
    schemas = [r["name"] for r in _query_rows(sql_s)]

    system_schemas = {
        "pg_catalog",
        "information_schema",
        "pg_toast",
        "pg_internal",
        "pglogical",
        "catalog_history",
    }
    schemas = [
        s
        for s in schemas
        if s not in system_schemas
        and not s.startswith("pg_temp")
        and not s.startswith("pg_toast_temp")
    ]

    lim = None
    if isinstance(limit_per_schema, int):
        lim = int(limit_per_schema)

    out = []
    sql_t = '''
    SELECT table_name
    FROM information_schema.tables
    WHERE table_type IN ('BASE TABLE','VIEW') AND table_schema = %(schema)s
    ORDER BY table_name
    '''
    for s in schemas:
        rows = _query_rows(sql_t, {"schema": s})
        names = [r["table_name"] for r in rows]
        if lim is not None:
            names = names[:lim]
        out.append({"schema": s, "tables": names})

    payload["ok"] = True
    payload["schemas"] = out
    print(json.dumps(payload, default=str))
except Exception as e:
    payload["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(payload, default=str))
""".strip()

        res = await execute_code(session.kernel, code, timeout=timeout)
        payload = _parse_last_line_json(res.stdout)
        return {
            "ok": _ok_from_payload_or_result(payload, res.status),
            "schemas": (payload or {}).get("schemas", []),
            "error": (payload or {}).get("error"),
            "stdout": res.stdout,
            "stderr": res.stderr,
        }

    @mcp.tool(
        title="PostgreSQL Reset",
        annotations=ToolAnnotations(
            title="PostgreSQL Reset",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    async def postgresql_reset(
        ctx: Context,
        notebook_path: str | None = None,
        connection_name: str = "pg_conn",
        timeout: float = 90.0,
    ) -> dict[str, Any]:
        """Forcibly re-initialize the connection variable from kernel env.

        Useful when Postgres was restarted and the existing connection/client
        is stale.
        """
        session = _get_session(ctx, notebook_path)
        await ctx.info(f"Resetting PostgreSQL connection variable: {connection_name}")

        await ensure_packages_with_report(
            session.kernel,
            ["psycopg[binary]", "agent-data-toolkit[postgresql]"],
        )

        vm = VariableManager(session.kernel)
        await vm.set("_mcp_pg_connection_name", connection_name)

        code = """
import json
import os

name = globals().get("_mcp_pg_connection_name", "pg_conn")
payload = {"ok": False, "error": None, "connection_name": name, "created": None}

def _dsn():
    return (
        os.environ.get("PG_DSN")
        or os.environ.get("POSTGRES_DSN")
        or os.environ.get("DATABASE_URL")
    )

try:
    old = globals().pop(name, None)
    if old is not None and hasattr(old, "close"):
        try:
            old.close()
        except Exception:
            pass

    dsn = _dsn()
    if not dsn:
        raise RuntimeError("No DSN set (PG_DSN/POSTGRES_DSN/DATABASE_URL)")

    obj = None
    try:
        from agent_data_toolkit.postgresql import ConnectionInfo, ConnectionManager, PostgresClient

        info = ConnectionInfo(dsn=dsn, connect_timeout=10)
        mgr = ConnectionManager(info)
        obj = PostgresClient(mgr)
        payload["created"] = "agent-data-toolkit.PostgresClient"
    except Exception:
        obj = None

    if obj is None:
        import psycopg

        obj = psycopg.connect(dsn)
        payload["created"] = "psycopg"

    globals()[name] = obj
    payload["ok"] = True
    print(json.dumps(payload, default=str))
except Exception as e:
    payload["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(payload, default=str))
""".strip()

        res = await execute_code(session.kernel, code, timeout=timeout)
        payload = _parse_last_line_json(res.stdout)
        return {
            "ok": _ok_from_payload_or_result(payload, res.status),
            "connection_name": connection_name,
            "created": (payload or {}).get("created"),
            "error": (payload or {}).get("error"),
            "stdout": res.stdout,
            "stderr": res.stderr,
        }

    @mcp.tool(
        title="PostgreSQL Close",
        annotations=ToolAnnotations(
            title="PostgreSQL Close",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def postgresql_close(
        ctx: Context,
        notebook_path: str | None = None,
        connection_name: str = "pg_conn",
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Close a PostgreSQL connection/client stored in a kernel variable.

        Supports both raw ``psycopg.Connection`` and
        ``agent_data_toolkit.postgresql.PostgresClient`` (calls ``close()``).
        """
        session = _get_session(ctx, notebook_path)
        await ctx.info(f"Closing PostgreSQL connection variable: {connection_name}")

        vm = VariableManager(session.kernel)
        await vm.set("_mcp_pg_connection_name", connection_name)

        code = """
import json
_name = globals().get("_mcp_pg_connection_name", "pg_conn")
_conn = globals().get(_name)

if _conn is None:
    print(
        json.dumps(
            {
                "ok": True,
                "closed": False,
                "reason": "variable not found",
                "connection_name": _name,
            }
        )
    )
else:
    try:
        # PostgresClient exposes close(); psycopg.Connection exposes close().
        if hasattr(_conn, "close"):
            _conn.close()
        print(json.dumps({"ok": True, "closed": True, "connection_name": _name}))
    except Exception as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "closed": False,
                    "connection_name": _name,
                    "error": f"{type(e).__name__}: {e}",
                }
            )
        )
""".strip()

        result = await invoke_code_cell(session, code, timeout=timeout)
        payload = _parse_last_line_json(result.stdout)
        return {
            "ok": _ok_from_payload_or_result(payload, result.status),
            "connection_name": connection_name,
            "cell_index": result.cell_index,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "parsed": payload,
        }


__all__ = ["register_postgresql_tools"]
