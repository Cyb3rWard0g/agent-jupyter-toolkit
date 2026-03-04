from __future__ import annotations

import logging

import pycrdt

BASE_LOGGER = "agent_jupyter_toolkit.notebook.transports.collab"
wslog = logging.getLogger(BASE_LOGGER + ".ws")


def hex_preview(b: bytes, n: int = 16) -> str:
    """Return a short hex preview for debug logs."""
    return b[:n].hex() + ("" if len(b) <= n else "...")


def looks_like_yws(b: bytes) -> tuple[bool, str]:
    """
    Strict-ish y-websocket classifier.
      byte0:
        0x00 -> SYNC; byte1 subtype: 0x00 STEP1, 0x01 STEP2, 0x02 UPDATE
        0x01 -> AWARENESS
        0x02 -> AUTH
    Returns: (ok, kind) where kind is 'sync:0|1|2', 'awareness', 'auth', or an error label.
    """
    if not b or len(b) < 2:
        return (False, "too-short")
    t = b[0]
    if t == 0x00:
        if len(b) < 3:
            return (False, "sync-too-short")
        sub = b[1]
        if sub not in (0x00, 0x01, 0x02):
            return (False, f"sync-bad-subtype:{sub}")
        return (True, f"sync:{sub}")
    if t == 0x01:
        return (True, "awareness")
    if t == 0x02:
        return (True, "auth")
    return (False, f"unknown-type:{t}")


def safe_handle_sync_message(message_data: bytes, doc, logger=None) -> tuple[bytes | None, bool]:
    """
    Safe wrapper around pycrdt.handle_sync_message.
    Returns: (reply_frame_or_None, applied_ok)
    """
    logger = logger or wslog
    handle_sync_message = getattr(pycrdt, "handle_sync_message", None)
    if handle_sync_message is None:
        return (None, False)
    try:
        reply = handle_sync_message(message_data, doc)  # type: ignore[misc]
        return (reply, True)
    except Exception as e:
        msg = str(e)
        lvl = "decode" if ("EndOfBuffer" in msg or "decode" in msg.lower()) else "sync"
        logger.debug("pycrdt %s error: %s", lvl, msg)
        return (None, False)
