"""
Helpers for building Jupyter wire-protocol messages and folding IOPub events
into a normalized `ExecutionResult`.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from typing import Any

from .types import ExecutionResult


def build_execute_request(
    code: str,
    *,
    silent: bool = False,
    store_history: bool = True,
    stop_on_error: bool = True,
    user_expressions: dict | None = None,
    allow_stdin: bool = False,
) -> dict[str, Any]:
    """
    Construct a minimal Jupyter 'execute_request' payload suitable for either
    ZMQ or WebSocket kernel channels.

    Notes
    -----
    - `silent=False` is required if you want iopub outputs to be emitted.
      (Transports may still harden these flags before sending.)
    - `allow_stdin=False` is typically correct for headless / agent use.

    Parameters
    ----------
    code : str
        Source code to execute.
    silent : bool, default False
        If True, the kernel will execute quietly (no outputs on iopub).
    store_history : bool, default True
        Whether the kernel should record this in its history.
    stop_on_error : bool, default True
        Stop executing queued code if an error is encountered.
    user_expressions : dict | None, default None
        Optional user expression dict per Jupyter protocol.
    allow_stdin : bool, default False
        Whether to allow stdin requests to be sent to the client.

    Returns
    -------
    dict
        A complete message envelope ready to be sent over a kernel channel.
    """
    return {
        "header": _mk_header("execute_request"),
        "parent_header": {},
        "metadata": {},
        "content": {
            "code": code,
            "silent": silent,
            "store_history": store_history,
            "user_expressions": user_expressions or {},
            "allow_stdin": allow_stdin,
            "stop_on_error": stop_on_error,
        },
        "channel": "shell",
        "buffers": [],  # keep the envelope consistent
    }


def build_complete_request(code: str, cursor_pos: int) -> dict[str, Any]:
    """
    Construct a Jupyter ``complete_request`` message.

    Parameters
    ----------
    code : str
        The code context for completion.
    cursor_pos : int
        Unicode offset of the cursor within *code*.

    Returns
    -------
    dict
        Message envelope for the shell channel.
    """
    return {
        "header": _mk_header("complete_request"),
        "parent_header": {},
        "metadata": {},
        "content": {
            "code": code,
            "cursor_pos": cursor_pos,
        },
        "channel": "shell",
        "buffers": [],
    }


def build_inspect_request(
    code: str,
    cursor_pos: int,
    detail_level: int = 0,
) -> dict[str, Any]:
    """
    Construct a Jupyter ``inspect_request`` message.

    Parameters
    ----------
    code : str
        The code context containing the object to inspect.
    cursor_pos : int
        Unicode offset of the cursor within *code*.
    detail_level : int
        0 for summary, 1 for full docs.

    Returns
    -------
    dict
        Message envelope for the shell channel.
    """
    return {
        "header": _mk_header("inspect_request"),
        "parent_header": {},
        "metadata": {},
        "content": {
            "code": code,
            "cursor_pos": cursor_pos,
            "detail_level": detail_level,
        },
        "channel": "shell",
        "buffers": [],
    }


def build_is_complete_request(code: str) -> dict[str, Any]:
    """
    Construct a Jupyter ``is_complete_request`` message.

    Parameters
    ----------
    code : str
        Source fragment to check for completeness.

    Returns
    -------
    dict
        Message envelope for the shell channel.
    """
    return {
        "header": _mk_header("is_complete_request"),
        "parent_header": {},
        "metadata": {},
        "content": {
            "code": code,
        },
        "channel": "shell",
        "buffers": [],
    }


def build_history_request(
    *,
    output: bool = False,
    raw: bool = True,
    hist_access_type: str = "tail",
    n: int = 10,
) -> dict[str, Any]:
    """
    Construct a Jupyter ``history_request`` message.

    Parameters
    ----------
    output : bool
        Include output with each history entry.
    raw : bool
        Return raw (un-transformed) input.
    hist_access_type : str
        One of ``"range"``, ``"tail"``, or ``"search"``.
    n : int
        Number of recent entries (for ``"tail"``) or max results.

    Returns
    -------
    dict
        Message envelope for the shell channel.
    """
    return {
        "header": _mk_header("history_request"),
        "parent_header": {},
        "metadata": {},
        "content": {
            "output": output,
            "raw": raw,
            "hist_access_type": hist_access_type,
            "session": 0,
            "start": 0,
            "stop": 0,
            "n": n,
            "pattern": "",
            "unique": False,
        },
        "channel": "shell",
        "buffers": [],
    }


def build_kernel_info_request() -> dict[str, Any]:
    """
    Construct a Jupyter ``kernel_info_request`` message.

    Returns
    -------
    dict
        Message envelope for the shell channel.
    """
    return {
        "header": _mk_header("kernel_info_request"),
        "parent_header": {},
        "metadata": {},
        "content": {},
        "channel": "shell",
        "buffers": [],
    }


def _mk_header(msg_type: str) -> dict[str, Any]:
    """Create a basic Jupyter header with an ISO-ish UTC timestamp."""
    return {
        "msg_id": uuid.uuid4().hex,
        "username": "agent-jupyter-toolkit",
        "session": uuid.uuid4().hex,
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "msg_type": msg_type,
        "version": "5.4",  # Updated to current Jupyter protocol version
    }


def fold_iopub_events(events: Iterable[dict]) -> ExecutionResult:
    """
    Fold an iterable of kernel events (iopub/shell) into a normalized ExecutionResult.

    Expected event msg_types include:
      - 'stream'
      - 'execute_result'
      - 'display_data' / 'update_display_data'
      - 'error'
      - 'clear_output'
      - 'execute_input'   (sets execution_count as soon as execution starts)
      - 'execute_reply'   (shell reply; may set execution_count/status)
      - 'status'          (execution_state='idle' marks end-of-exec)

    This is tolerant of frames that may already be partially-normalized; it
    prefers explicit keys but falls back to header/content when needed.
    """
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    outputs: list[dict] = []
    exec_count: int | None = None
    status = "ok"

    for ev in events:
        # Prefer explicit msg_type; fall back to header.msg_type
        msg_type = ev.get("msg_type") or ev.get("header", {}).get("msg_type")
        if not msg_type:
            continue
        content = ev.get("content") or {}

        if msg_type == "stream":
            # {'name': 'stdout'|'stderr', 'text': str}
            name = content.get("name")
            text = content.get("text", "") or ""
            outputs.append({"output_type": "stream", "name": name, "text": text})
            if name == "stdout":
                stdout_parts.append(text)
            elif name == "stderr":
                stderr_parts.append(text)

        elif msg_type in ("display_data", "update_display_data", "execute_result"):
            # Treat update_display_data like display_data for our purposes.
            normalized_type = "display_data" if msg_type == "update_display_data" else msg_type
            data = content.get("data") or {}
            md = content.get("metadata") or {}
            out = {
                "output_type": normalized_type,
                "data": data,
                "metadata": md,
            }
            if normalized_type == "execute_result":
                if content.get("execution_count") is not None:
                    exec_count = content["execution_count"]
                out["execution_count"] = exec_count
            outputs.append(out)

        elif msg_type == "error":
            # {'ename': str, 'evalue': str, 'traceback': list[str]}
            status = "error"
            outputs.append(
                {
                    "output_type": "error",
                    "ename": content.get("ename"),
                    "evalue": content.get("evalue"),
                    "traceback": content.get("traceback"),
                }
            )

        elif msg_type == "clear_output":
            # Align with local ZMQ behavior: clear prior cell outputs/streams.
            outputs.clear()
            stdout_parts.clear()
            stderr_parts.clear()

        elif msg_type == "execute_input":
            # {'execution_count': int, 'code': str}
            if content.get("execution_count") is not None:
                exec_count = content["execution_count"]

        elif msg_type == "execute_reply":
            # Shell reply often carries the definitive execution_count and status.
            if content.get("execution_count") is not None:
                exec_count = content["execution_count"]
            # Only override status if we haven't already seen an explicit error.
            if status != "error":
                status = content.get("status", status)

        elif msg_type == "status":
            # 'idle' indicates the kernel finished processing this request
            # (the caller decides loop termination; we just fold).
            pass

        # Other msg_types (e.g., 'comm_*') can be added as needed.

    return ExecutionResult(
        status=status,
        execution_count=exec_count,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
        outputs=outputs,
    )
