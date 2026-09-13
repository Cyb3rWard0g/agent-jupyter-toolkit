"""
Test mimetypes and serialization in kernel.
"""

import json

import pytest

from agent_jupyter_toolkit.kernel import SessionConfig, create_session, serialization

pytestmark = pytest.mark.asyncio


async def test_kernel_mimetypes():
    sess = create_session(SessionConfig(mode="local", kernel_name="python3"))
    await sess.start()
    try:
        # Test serialization
        data = {"a": 1, "b": 2}
        ser = serialization.serialize_value(data)
        deser = serialization.deserialize_value(ser["data"], ser["metadata"])
        assert deser == data
        # Test mimetypes using standard library
        import mimetypes as std_mimetypes

        mt, _ = std_mimetypes.guess_type("test.png")
        assert mt == "image/png"
    finally:
        await sess.shutdown()


async def test_serialization_survives_json_transport_boundary():
    payload = serialization.serialize_value({"items": [1, 2, 3]})
    transported = json.loads(json.dumps(payload))

    assert serialization.deserialize_value(transported["data"], transported["metadata"]) == {
        "items": [1, 2, 3]
    }


async def test_deserialization_accepts_native_json_mime_values():
    assert serialization.deserialize_value(
        {"application/json": {"items": [1, 2, 3]}},
        {},
    ) == {"items": [1, 2, 3]}
