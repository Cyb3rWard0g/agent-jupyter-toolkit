"""Tests for cell-level read methods on transports and NotebookSession."""

import pytest

from agent_jupyter_toolkit.kernel import SessionConfig, create_session
from agent_jupyter_toolkit.notebook import NotebookSession, make_document_transport

pytestmark = pytest.mark.asyncio


# ── LocalFileDocumentTransport ────────────────────────


async def test_local_cell_count_empty(tmp_path):
    """Empty notebook should have 0 cells (or 1 if nbformat adds a default)."""
    doc = make_document_transport(
        mode="local",
        local_path=str(tmp_path / "empty.ipynb"),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
        create_if_missing=True,
    )
    await doc.start()
    count = await doc.cell_count()
    assert count == 0


async def test_local_get_cell_and_source(tmp_path):
    """Append cells and read them back individually."""
    doc = make_document_transport(
        mode="local",
        local_path=str(tmp_path / "cells.ipynb"),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
        create_if_missing=True,
    )
    await doc.start()

    await doc.append_code_cell("x = 1")
    await doc.append_markdown_cell("# Hello")
    await doc.append_code_cell("y = 2")

    assert await doc.cell_count() == 3

    # get_cell returns full cell dict
    cell0 = await doc.get_cell(0)
    assert cell0["cell_type"] == "code"
    assert cell0["source"] == "x = 1"

    cell1 = await doc.get_cell(1)
    assert cell1["cell_type"] == "markdown"
    assert cell1["source"] == "# Hello"

    cell2 = await doc.get_cell(2)
    assert cell2["cell_type"] == "code"
    assert cell2["source"] == "y = 2"

    # get_cell_source returns just the source string
    assert await doc.get_cell_source(0) == "x = 1"
    assert await doc.get_cell_source(1) == "# Hello"
    assert await doc.get_cell_source(2) == "y = 2"


async def test_local_get_cell_index_error(tmp_path):
    """Out-of-range index should raise IndexError."""
    doc = make_document_transport(
        mode="local",
        local_path=str(tmp_path / "err.ipynb"),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
        create_if_missing=True,
    )
    await doc.start()

    with pytest.raises(IndexError, match="out of range"):
        await doc.get_cell(0)

    with pytest.raises(IndexError, match="out of range"):
        await doc.get_cell_source(0)


async def test_local_get_cell_negative_index(tmp_path):
    """Negative index should raise IndexError."""
    doc = make_document_transport(
        mode="local",
        local_path=str(tmp_path / "neg.ipynb"),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
        create_if_missing=True,
    )
    await doc.start()
    await doc.append_code_cell("x = 1")

    with pytest.raises(IndexError):
        await doc.get_cell(-1)


# ── NotebookSession (end-to-end with local transport) ─


async def test_session_cell_reads_after_execution(tmp_path):
    """NotebookSession exposes cell reads that reflect executed code."""
    notebook_path = tmp_path / "session_cells.ipynb"
    kernel_session = create_session(SessionConfig(mode="local", kernel_name="python3"))
    doc_transport = make_document_transport(
        mode="local",
        local_path=str(notebook_path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
        create_if_missing=True,
    )
    nb_session = NotebookSession(kernel=kernel_session, doc=doc_transport)

    async with nb_session:
        await nb_session.append_and_run("x = 42")
        await nb_session.append_and_run("print(x)")
        await nb_session.run_markdown("# Results")

        assert await nb_session.cell_count() == 3

        cell0 = await nb_session.get_cell(0)
        assert cell0["cell_type"] == "code"
        assert cell0["source"] == "x = 42"

        assert await nb_session.get_cell_source(1) == "print(x)"
        assert await nb_session.get_cell_source(2) == "# Results"

        cell2 = await nb_session.get_cell(2)
        assert cell2["cell_type"] == "markdown"


# ── NoopDoc fallback ─────────────────────────────────


async def test_noop_doc_cell_reads():
    """NoopDoc should have 0 cells and raise IndexError on reads."""
    doc = make_document_transport(
        mode="invalid",  # triggers NoopDoc
        local_path=None,
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
    )
    assert await doc.cell_count() == 0

    with pytest.raises(IndexError):
        await doc.get_cell(0)

    with pytest.raises(IndexError):
        await doc.get_cell_source(0)
