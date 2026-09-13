from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import nbformat
import pycrdt
import pytest
from jupyter_ydoc import YNotebook
from nbformat.validator import NotebookValidationError
from nbformat.warnings import DuplicateCellId

from agent_jupyter_toolkit.kernel.messages import fold_iopub_events
from agent_jupyter_toolkit.kernel.types import ExecutionResult, KernelDisconnectedError
from agent_jupyter_toolkit.notebook import NotebookSession
from agent_jupyter_toolkit.notebook.transports.collab.transport import (
    CollabYjsDocumentTransport,
)
from agent_jupyter_toolkit.notebook.transports.collab.yutils import make_code_cell_dict
from agent_jupyter_toolkit.notebook.transports.local_file import LocalFileDocumentTransport
from agent_jupyter_toolkit.notebook.utils import normalize_notebook, validate_notebook
from agent_jupyter_toolkit.utils.execution import invoke_code_cell, invoke_existing_cell

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "failure", [RuntimeError("document startup failed"), asyncio.CancelledError()]
)
async def test_failed_document_start_cleans_partial_resources(failure):
    kernel = AsyncMock()
    document = AsyncMock()
    document.start.side_effect = failure
    session = NotebookSession(kernel=kernel, doc=document)

    with pytest.raises(type(failure)):
        await session.start()

    document.stop.assert_awaited_once()
    kernel.shutdown.assert_awaited_once()


class MutatingKernel:
    def __init__(self, mutation) -> None:
        self._mutation = mutation

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def is_alive(self) -> bool:
        return True

    async def execute(self, _code, *, output_callback=None, **_kwargs) -> ExecutionResult:
        await self._mutation()
        outputs = [{"output_type": "stream", "name": "stdout", "text": "done\n"}]
        if output_callback:
            await output_callback(outputs, 1)
        return ExecutionResult(
            status="ok",
            execution_count=1,
            stdout="done\n",
            outputs=outputs,
        )


async def test_insert_during_execution_does_not_redirect_outputs(tmp_path):
    doc = LocalFileDocumentTransport(str(tmp_path / "identity.ipynb"))

    async def insert_before_target():
        await doc.insert_markdown_cell(0, "# inserted")

    session = NotebookSession(kernel=MutatingKernel(insert_before_target), doc=doc)
    async with session:
        index, result = await session.append_and_run("print('target')")
        target = await doc.get_cell(index)

    assert index == 1
    assert target["source"] == "print('target')"
    assert target["outputs"][0]["text"] == "done\n"
    assert result.cell_id == target["id"]
    assert result.persistence_status == "ok"
    assert (await doc.get_cell(0))["cell_type"] == "markdown"


async def test_source_change_returns_persistence_conflict(tmp_path):
    doc = LocalFileDocumentTransport(str(tmp_path / "source-conflict.ipynb"))

    async def change_target():
        await doc.set_cell_source(0, "print('edited')")

    session = NotebookSession(kernel=MutatingKernel(change_target), doc=doc)
    async with session:
        _, result = await session.append_and_run("print('original')")
        target = await doc.get_cell(0)

    assert result.status == "ok"
    assert result.persistence_status == "error"
    assert "CellSourceChangedError" in (result.persistence_error or "")
    assert target["source"] == "print('edited')"
    assert target["outputs"] == []


async def test_invalid_output_mutations_never_replace_local_file(tmp_path):
    path = tmp_path / "valid.ipynb"
    doc = LocalFileDocumentTransport(str(path))
    await doc.start()
    await doc.append_markdown_cell("# title")
    before = path.read_text()

    with pytest.raises(TypeError, match="code cell"):
        await doc.update_cell_outputs(
            0,
            [{"output_type": "stream", "name": "stdout", "text": "bad"}],
            1,
        )
    assert path.read_text() == before
    nbformat.validate(nbformat.read(path, as_version=4))

    invalid = await doc.fetch()
    invalid["cells"][0]["outputs"] = []
    with pytest.raises(NotebookValidationError):
        await doc.save(invalid)
    assert path.read_text() == before


async def test_invalid_output_does_not_poison_debounced_notebook(tmp_path):
    path = tmp_path / "debounced.ipynb"
    doc = LocalFileDocumentTransport(str(path), autosave_delay=10)
    await doc.start()
    await doc.append_code_cell("print('valid')")
    with pytest.raises(NotebookValidationError):
        await doc.update_cell_outputs(0, [{"output_type": "invalid"}], 1)
    assert (await doc.get_cell(0))["outputs"] == []
    await doc.stop()
    notebook = nbformat.read(path, as_version=4)
    nbformat.validate(notebook)
    assert notebook.cells[0].source == "print('valid')"


async def test_collaborative_move_preserves_shared_cell_types(monkeypatch):
    transport = CollabYjsDocumentTransport("http://unused", "move.ipynb")
    doc = pycrdt.Doc()
    notebook = YNotebook(doc)
    transport._doc = doc
    transport._ynb = notebook
    transport._cells = notebook.ycells
    transport._initial_sync_done.set()
    monkeypatch.setattr(transport, "_broadcast_update", AsyncMock())

    with doc.transaction():
        for source in ("a = 1", "b = 2", "c = 3"):
            notebook.ycells.append(notebook.create_ycell(make_code_cell_dict(source, None, None)))

    await transport.move_cell(0, 2)
    moved = await transport.fetch()

    assert [cell["source"] for cell in moved["cells"]] == ["b = 2", "c = 3", "a = 1"]
    assert all(isinstance(notebook.ycells[index], pycrdt.Map) for index in range(3))
    assert (await transport.get_cell(2))["source"] == "a = 1"


async def test_collaborative_output_update_preserves_concurrent_source_edit(monkeypatch):
    doc_a = pycrdt.Doc()
    notebook_a = YNotebook(doc_a)
    with doc_a.transaction():
        ycell = notebook_a.create_ycell(make_code_cell_dict("initial", None, None))
        notebook_a.ycells.append(ycell)
        cell_id = ycell["id"]

    doc_b = pycrdt.Doc()
    doc_b.apply_update(doc_a.get_update())
    notebook_b = YNotebook(doc_b)
    state_a = doc_a.get_state()
    state_b = doc_b.get_state()

    transport_a = CollabYjsDocumentTransport("http://unused", "shared.ipynb")
    transport_a._doc = doc_a
    transport_a._ynb = notebook_a
    transport_a._cells = notebook_a.ycells
    transport_b = CollabYjsDocumentTransport("http://unused", "shared.ipynb")
    transport_b._doc = doc_b
    transport_b._ynb = notebook_b
    transport_b._cells = notebook_b.ycells
    for transport in (transport_a, transport_b):
        transport._initial_sync_done.set()
        monkeypatch.setattr(transport, "_broadcast_update", AsyncMock())

    await transport_b.set_cell_source_by_id(cell_id, "collaborator edit", expected_source="initial")
    await transport_a.update_cell_outputs_by_id(
        cell_id,
        [{"output_type": "stream", "name": "stdout", "text": "done\n"}],
        1,
        expected_source="initial",
    )

    doc_a.apply_update(doc_b.get_update(state_b))
    doc_b.apply_update(doc_a.get_update(state_a))
    for transport in (transport_a, transport_b):
        cell = await transport.get_cell_by_id(cell_id)
        assert cell["source"] == "collaborator edit"
        assert cell["outputs"][0]["text"] == "done\n"


async def test_collaborative_metadata_updates_persist_and_merge_by_key(monkeypatch):
    doc_a = pycrdt.Doc()
    notebook_a = YNotebook(doc_a)
    notebook_a.set(nbformat.v4.new_notebook(metadata={"existing": "keep"}))
    doc_b = pycrdt.Doc()
    doc_b.apply_update(doc_a.get_update())
    notebook_b = YNotebook(doc_b)
    state_a = doc_a.get_state()
    state_b = doc_b.get_state()

    transports = []
    for doc, notebook in ((doc_a, notebook_a), (doc_b, notebook_b)):
        transport = CollabYjsDocumentTransport("http://unused", "metadata.ipynb")
        transport._doc = doc
        transport._ynb = notebook
        transport._cells = notebook.ycells
        transport._initial_sync_done.set()
        monkeypatch.setattr(transport, "_broadcast_update", AsyncMock())
        transports.append(transport)

    await transports[0].update_metadata({"first": {"version": "1"}})
    await transports[1].update_metadata({"second": [1, 2]})

    doc_a.apply_update(doc_b.get_update(state_b))
    doc_b.apply_update(doc_a.get_update(state_a))
    for transport in transports:
        metadata = await transport.get_metadata()
        assert metadata["existing"] == "keep"
        assert metadata["first"] == {"version": "1"}
        assert metadata["second"] == [1, 2]
        transport._broadcast_update.assert_awaited_once()


async def test_collaborative_dependency_entries_merge_across_clients(monkeypatch):
    doc_a = pycrdt.Doc()
    notebook_a = YNotebook(doc_a)
    notebook_a.set(
        nbformat.v4.new_notebook(metadata={"agent_dependencies": {"base": {"version": "1"}}})
    )
    doc_b = pycrdt.Doc()
    doc_b.apply_update(doc_a.get_update())
    notebook_b = YNotebook(doc_b)
    state_a = doc_a.get_state()
    state_b = doc_b.get_state()

    transports = []
    for doc, notebook in ((doc_a, notebook_a), (doc_b, notebook_b)):
        transport = CollabYjsDocumentTransport("http://unused", "dependencies.ipynb")
        transport._doc = doc
        transport._ynb = notebook
        transport._cells = notebook.ycells
        transport._initial_sync_done.set()
        monkeypatch.setattr(transport, "_broadcast_update", AsyncMock())
        transports.append(transport)

    async def package_versions(_kernel, packages, *, timeout):
        del timeout
        return {packages[0]: "2" if packages[0] == "numpy" else "3"}

    monkeypatch.setattr(
        "agent_jupyter_toolkit.utils.packages.get_package_versions",
        package_versions,
    )
    sessions = [
        NotebookSession(kernel=SimpleNamespace(), doc=transports[0]),
        NotebookSession(kernel=SimpleNamespace(), doc=transports[1]),
    ]

    await asyncio.gather(
        sessions[0]._track_dependencies(["numpy"]),
        sessions[1]._track_dependencies(["pandas"]),
    )

    update_a = doc_a.get_update(state_a)
    update_b = doc_b.get_update(state_b)
    doc_a.apply_update(update_b)
    doc_b.apply_update(update_a)
    await asyncio.gather(
        transports[0]._sync_metadata_map_snapshots(),
        transports[1]._sync_metadata_map_snapshots(),
    )

    for session, notebook in zip(sessions, (notebook_a, notebook_b), strict=True):
        dependencies = await session.get_tracked_dependencies()
        assert set(dependencies) == {"base", "numpy", "pandas"}
        assert notebook.source["metadata"]["agent_dependencies"] == dependencies


async def test_collaborative_placeholder_cleanup_preserves_meaningful_blank_cell(monkeypatch):
    transport = CollabYjsDocumentTransport("http://unused", "blank.ipynb")
    doc = pycrdt.Doc()
    notebook = YNotebook(doc)
    transport._doc = doc
    transport._ynb = notebook
    transport._cells = notebook.ycells
    monkeypatch.setattr(transport, "_broadcast_update", AsyncMock())
    with doc.transaction():
        notebook.ycells.append(
            notebook.create_ycell(
                {
                    **make_code_cell_dict("", {"tags": ["keep"]}, None),
                    "outputs": [{"output_type": "stream", "name": "stdout", "text": "saved"}],
                }
            )
        )

    await transport._strip_default_empty_cell()

    assert await transport.cell_count() == 1
    assert (await transport.get_cell(0))["metadata"]["tags"] == ["keep"]


async def test_run_at_keeps_original_cell_identity_during_reorder(tmp_path):
    class ReorderingDocument(LocalFileDocumentTransport):
        async def set_cell_source_by_id(self, cell_id, source, *, expected_source=None):
            await self.insert_markdown_cell(0, "# collaborator")
            return await super().set_cell_source_by_id(
                cell_id,
                source,
                expected_source=expected_source,
            )

    async def no_mutation():
        return None

    doc = ReorderingDocument(str(tmp_path / "reorder.ipynb"))
    async with NotebookSession(kernel=MutatingKernel(no_mutation), doc=doc) as session:
        await doc.append_code_cell("original")
        original_id = (await doc.get_cell(0))["id"]
        result = await session.run_at(0, "updated")

    assert result.cell_id == original_id
    assert (await doc.get_cell(0))["cell_type"] == "markdown"
    assert (await doc.get_cell(1))["source"] == "updated"


async def test_validation_is_non_mutating_and_normalization_is_explicit():
    notebook = nbformat.v4.new_notebook()
    first = nbformat.v4.new_code_cell("a = 1", id="duplicate")
    second = nbformat.v4.new_code_cell("b = 2", id="duplicate")
    notebook.cells.extend([first, second])

    with pytest.raises(ValueError, match="Duplicate notebook cell id"):
        validate_notebook(notebook)
    assert [cell.id for cell in notebook.cells] == ["duplicate", "duplicate"]

    with pytest.warns(DuplicateCellId, match="Non-unique cell id"):
        changes, normalized = normalize_notebook(notebook)
    assert changes == 1
    assert normalized.cells[0].id != normalized.cells[1].id
    assert [cell.id for cell in notebook.cells] == ["duplicate", "duplicate"]


async def test_collaboration_send_failure_is_reported_as_persistence_error():
    doc = pycrdt.Doc()
    notebook = YNotebook(doc)
    with doc.transaction():
        ycell = notebook.create_ycell(make_code_cell_dict("print('value')", None, None))
        notebook.ycells.append(ycell)
        cell_id = ycell["id"]

    transport = CollabYjsDocumentTransport("http://unused", "offline.ipynb")
    transport._doc = doc
    transport._ynb = notebook
    transport._cells = notebook.ycells
    transport._initial_sync_done.set()
    transport._last_broadcast_state = doc.get_state()
    transport._ws = SimpleNamespace(
        closed=False,
        send_bytes=AsyncMock(side_effect=OSError("offline")),
    )

    async def no_mutation():
        return None

    session = NotebookSession(kernel=MutatingKernel(no_mutation), doc=transport)
    session._started = True
    result = await session._execute_with_streaming(
        "print('value')",
        0,
        cell_id=cell_id,
    )

    assert result.status == "ok"
    assert result.persistence_status == "error"
    assert "Could not send collaborative update" in (result.persistence_error or "")


class DisplayKernel(MutatingKernel):
    generation = 0

    def __init__(self, results):
        self.results = iter(results)

    def session_info(self):
        return SimpleNamespace(kernel_generation=self.generation)

    async def execute(self, _code, **_kwargs):
        return next(self.results)


def display_result(value, *types):
    return fold_iopub_events(
        [
            {
                "msg_type": kind,
                "content": {"data": {"text/plain": value}, "transient": {"display_id": "shared"}},
            }
            for kind in types
        ]
    )


@pytest.mark.parametrize("new_display", [False, True])
async def test_shared_display_updates_every_registered_cell(tmp_path, new_display):
    doc = LocalFileDocumentTransport(str(tmp_path / "shared.ipynb"))
    kinds = ["display_data", "update_display_data"] if new_display else ["update_display_data"]
    kernel = DisplayKernel(
        [
            display_result("before", "display_data"),
            display_result("before", "display_data"),
            display_result("after", *kinds),
        ]
    )
    async with NotebookSession(kernel=kernel, doc=doc) as session:
        await session.append_and_run("first")
        await session.append_and_run("second")
        _, result = await session.append_and_run("update")
        assert result.persistence_status == "ok"
        for index in (0, 1):
            assert (await doc.get_cell(index))["outputs"][0]["data"]["text/plain"] == "after"


@pytest.mark.parametrize("invalidation", ["rerun", "external-output", "restart"])
async def test_display_updates_do_not_overwrite_replaced_outputs(tmp_path, invalidation):
    doc = LocalFileDocumentTransport(str(tmp_path / "stale-display.ipynb"))
    original = display_result("before", "display_data")
    replacement = display_result("replacement", "display_data")
    replacement.display_ids.clear()
    update = display_result("after", "update_display_data")
    kernel = DisplayKernel(
        [original, replacement, update] if invalidation == "rerun" else [original, update]
    )
    async with NotebookSession(kernel=kernel, doc=doc) as session:
        await session.append_and_run("same source")
        if invalidation == "rerun":
            await session.run_at(0, "same source")
        elif invalidation == "external-output":
            await doc.update_cell_outputs(0, replacement.outputs, 2)
        else:
            kernel.generation += 1
        before = (await doc.get_cell(0))["outputs"]
        _, result = await session.append_and_run("update old handle")
        assert result.persistence_status == "ok"
        assert (await doc.get_cell(0))["outputs"] == before


async def test_display_update_discards_a_deleted_target(tmp_path):
    doc = LocalFileDocumentTransport(str(tmp_path / "deleted-display.ipynb"))
    kernel = DisplayKernel(
        [
            display_result("before", "display_data"),
            display_result("after", "update_display_data"),
        ]
    )
    async with NotebookSession(kernel=kernel, doc=doc) as session:
        await session.append_and_run("create handle")
        await doc.delete_cell(0)
        _, result = await session.append_and_run("update deleted handle")

    assert result.status == "ok"
    assert result.persistence_status == "ok"


async def test_disconnect_keeps_partial_outputs_and_identity_without_replay(tmp_path):
    doc = LocalFileDocumentTransport(str(tmp_path / "disconnect.ipynb"))
    kernel = AsyncMock()
    del kernel.session_info
    partial = ExecutionResult(
        stdout="already ran",
        request_id="request-1",
        kernel_generation=3,
        outputs=[{"output_type": "stream", "name": "stdout", "text": "already ran"}],
    )
    kernel.execute.side_effect = KernelDisconnectedError("connection lost", partial_result=partial)
    async with NotebookSession(kernel=kernel, doc=doc) as session:
        result = await invoke_code_cell(session, "side_effect()")
        cell = await doc.get_cell(0)
    assert kernel.execute.await_count == 1
    assert result.outcome == "unknown"
    assert result.status == "error"
    assert result.cell_id == cell["id"]
    assert result.request_id == "request-1"
    assert result.kernel_generation == 3
    assert result.persistence_status == "ok"
    assert cell["outputs"] == partial.outputs


async def test_existing_cell_forwards_timeout_and_preserves_partial_result():
    notebook = AsyncMock()
    notebook.run_at.return_value = ExecutionResult(
        status="error",
        outcome="unknown",
        timed_out=True,
        stdout="partial",
    )
    result = await invoke_existing_cell(notebook, 2, "slow()", timeout=0.25)
    notebook.run_at.assert_awaited_once_with(2, "slow()", timeout=0.25)
    assert result.outcome == "unknown"
    assert result.timed_out
    assert result.stdout == "partial"


async def test_run_all_reports_document_conflict_as_failure(tmp_path):
    doc = LocalFileDocumentTransport(str(tmp_path / "run-all-conflict.ipynb"))

    async def edit():
        await doc.set_cell_source(0, "changed")

    async with NotebookSession(kernel=MutatingKernel(edit), doc=doc) as session:
        await doc.append_code_cell("original")
        result = await session.run_all()
    assert result.status == "error"
    assert result.cells[0].status == "ok"
    assert result.cells[0].persistence_status == "error"
    assert result.first_failure is result.cells[0]
