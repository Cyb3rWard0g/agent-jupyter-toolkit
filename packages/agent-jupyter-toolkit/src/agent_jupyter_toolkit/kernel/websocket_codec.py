"""Jupyter Server's default binary WebSocket framing."""

from __future__ import annotations

import json
import struct
from typing import Any


class WebSocketFrameError(ValueError):
    """Raised when a kernel-channel binary frame is malformed."""


def deserialize_binary_message(data: bytes | bytearray | memoryview) -> dict[str, Any]:
    """Decode the offset-framed message used by Jupyter Server kernel channels."""
    frame = bytes(data)
    if len(frame) < 8:
        raise WebSocketFrameError("Binary kernel message is shorter than its header")
    (buffer_count,) = struct.unpack("!I", frame[:4])
    if buffer_count < 1:
        raise WebSocketFrameError("Binary kernel message has no JSON envelope")
    header_size = 4 * (buffer_count + 1)
    if header_size > len(frame):
        raise WebSocketFrameError("Binary kernel message has a truncated offset table")
    offsets = struct.unpack(f"!{buffer_count}I", frame[4:header_size])
    if offsets[0] != header_size:
        raise WebSocketFrameError("Binary kernel message has an invalid first offset")
    if tuple(offsets) != tuple(sorted(offsets)) or offsets[-1] > len(frame):
        raise WebSocketFrameError("Binary kernel message has invalid buffer offsets")
    ends = (*offsets[1:], len(frame))
    buffers = [frame[start:end] for start, end in zip(offsets, ends, strict=True)]
    try:
        message = json.loads(buffers[0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebSocketFrameError("Binary kernel message has an invalid JSON envelope") from exc
    if not isinstance(message, dict):
        raise WebSocketFrameError("Binary kernel message JSON envelope must be an object")
    message["buffers"] = buffers[1:]
    return message


def serialize_binary_message(message: dict[str, Any]) -> bytes:
    """Encode one kernel message and its buffers using default offset framing."""
    envelope = dict(message)
    raw_buffers = envelope.pop("buffers", []) or []
    buffers = [
        json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        *(bytes(buffer) for buffer in raw_buffers),
    ]
    header_size = 4 * (len(buffers) + 1)
    offsets: list[int] = []
    offset = header_size
    for buffer in buffers:
        offsets.append(offset)
        offset += len(buffer)
    header = struct.pack(f"!{len(buffers) + 1}I", len(buffers), *offsets)
    return b"".join((header, *buffers))
