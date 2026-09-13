from unittest.mock import AsyncMock

import aiohttp
import pytest

from agent_jupyter_toolkit.notebook.factory import make_document_transport
from agent_jupyter_toolkit.notebook.transports.collab import CollabYjsDocumentTransport
from agent_jupyter_toolkit.notebook.transports.contents import ContentsApiDocumentTransport
from agent_jupyter_toolkit.notebook.transports.fallback import (
    CollaborationFallbackTransport,
)

pytestmark = pytest.mark.asyncio


def _http_error(status: int) -> aiohttp.ClientResponseError:
    return aiohttp.ClientResponseError(
        request_info=None, history=(), status=status, message="failure"
    )


async def test_unsupported_collaboration_api_falls_back_to_contents():
    collaboration = AsyncMock()
    collaboration.start.side_effect = _http_error(404)
    contents = AsyncMock()
    contents.fetch.return_value = {"cells": []}
    transport = CollaborationFallbackTransport(collaboration, contents)

    await transport.start()

    assert transport.selected_transport == "contents"
    assert await transport.fetch() == {"cells": []}
    contents.start.assert_awaited_once()


async def test_fallback_restart_reselects_collaboration_and_start_is_idempotent():
    collaboration = AsyncMock()
    collaboration.start.side_effect = [_http_error(404), None]
    contents = AsyncMock()
    transport = CollaborationFallbackTransport(collaboration, contents)
    await transport.start()
    await transport.start()
    assert collaboration.start.await_count == 1
    await transport.stop()
    contents.stop.assert_awaited_once()
    await transport.start()
    assert transport.selected_transport == "collaboration"
    assert transport.fallback_reason is None
    await transport.stop()


async def test_collaboration_auth_failure_does_not_fall_back():
    collaboration = AsyncMock()
    collaboration.start.side_effect = _http_error(403)
    transport = CollaborationFallbackTransport(collaboration, AsyncMock())

    with pytest.raises(aiohttp.ClientResponseError):
        await transport.start()


async def test_factory_exposes_required_preferred_and_disabled_modes():
    common = {
        "mode": "server",
        "local_path": None,
        "remote_base": "http://unused",
        "remote_path": "test.ipynb",
        "token": None,
        "headers_json": None,
    }

    required = make_document_transport(**common, collaboration_mode="required")
    preferred = make_document_transport(**common, collaboration_mode="preferred")
    disabled = make_document_transport(**common, collaboration_mode="disabled")

    assert isinstance(required, CollabYjsDocumentTransport)
    assert required.collaboration_mode == "required"
    assert isinstance(preferred, CollaborationFallbackTransport)
    assert preferred.collaboration_mode == "preferred"
    assert isinstance(disabled, ContentsApiDocumentTransport)
    assert disabled.collaboration_mode == "disabled"


async def test_factory_rejects_unknown_collaboration_mode():
    with pytest.raises(ValueError, match="collaboration_mode"):
        make_document_transport(
            "server",
            local_path=None,
            remote_base="http://unused",
            remote_path="test.ipynb",
            token=None,
            headers_json=None,
            collaboration_mode="sometimes",
        )
