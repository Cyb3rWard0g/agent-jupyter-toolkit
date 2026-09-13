import pytest

from agent_jupyter_toolkit.notebook import execute_notebook_batch

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("options", [{}, {"timeout": 30}])
async def test_nbclient_batch_execution_preserves_clear_and_display_updates(options):
    pytest.importorskip("nbclient")
    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "id": "batch-cell",
                "metadata": {},
                "outputs": [],
                "source": (
                    "from IPython.display import display\n"
                    "handle = display('before', display_id=True)\n"
                    "handle.update('after')"
                ),
            }
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }

    result = await execute_notebook_batch(notebook, **options)

    outputs = result["cells"][0]["outputs"]
    assert len(outputs) == 1
    assert "after" in outputs[0]["data"]["text/plain"]
    assert notebook["cells"][0]["outputs"] == []
