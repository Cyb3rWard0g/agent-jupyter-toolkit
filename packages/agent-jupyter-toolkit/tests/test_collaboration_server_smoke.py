import asyncio
import os

import pytest

from agent_jupyter_toolkit.utils import create_notebook_transport

pytestmark = pytest.mark.asyncio

skip_collaboration = pytest.mark.skipif(
    "JAT_COLLAB_URL" not in os.environ,
    reason="Set JAT_COLLAB_URL to run collaboration server tests.",
)


@skip_collaboration
async def test_two_collaboration_clients_converge():
    options = {
        "mode": "remote",
        "path": "collaboration-convergence.ipynb",
        "base_url": os.environ["JAT_COLLAB_URL"].rstrip("/"),
        "token": os.getenv("JAT_COLLAB_TOKEN"),
        "prefer_collab": True,
        "create_if_missing": True,
    }
    first = create_notebook_transport(**options)
    second = create_notebook_transport(**options)
    await first.start()
    await second.start()
    try:
        assert first.selected_transport == "collaboration"
        assert second.selected_transport == "collaboration"
        await first.append_markdown_cell("# converged")

        for _attempt in range(30):
            cells = (await second.fetch()).get("cells", [])
            if any(cell.get("source") == "# converged" for cell in cells):
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Second collaboration client did not receive the first client's edit")
    finally:
        await second.stop()
        await first.stop()
