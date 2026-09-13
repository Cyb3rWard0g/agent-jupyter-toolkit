import struct

import pytest

from agent_jupyter_toolkit.kernel.websocket_codec import (
    WebSocketFrameError,
    deserialize_binary_message,
    serialize_binary_message,
)


def test_binary_kernel_message_round_trip_preserves_buffers():
    message = {
        "header": {"msg_id": "m1", "msg_type": "display_data"},
        "parent_header": {"msg_id": "p1"},
        "metadata": {},
        "content": {"data": {"text/plain": "buffered"}},
        "channel": "iopub",
        "buffers": [b"abc", memoryview(b"def")],
    }

    decoded = deserialize_binary_message(serialize_binary_message(message))

    assert decoded["content"] == message["content"]
    assert decoded["buffers"] == [b"abc", b"def"]


@pytest.mark.parametrize(
    "frame",
    [
        b"tiny",
        struct.pack("!II", 0, 8),
        struct.pack("!II", 1, 99),
    ],
)
def test_binary_kernel_message_rejects_bad_offsets(frame):
    with pytest.raises(WebSocketFrameError):
        deserialize_binary_message(frame)
