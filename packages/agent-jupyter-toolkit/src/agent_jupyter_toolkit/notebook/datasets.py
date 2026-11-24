"""Save and replay immutable string/null tables in a notebook workspace."""

from __future__ import annotations

import asyncio
import hashlib
import json
import keyword
import re
import uuid
from importlib.util import find_spec
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

from aiohttp import ClientError

from ..kernel.types import KernelDisconnectedError
from ._dataset_store import (
    MAX_BATCH_BYTES,
    MAX_BATCHES,
    MAX_COLUMNS,
    MAX_METADATA_BYTES,
    MAX_ROWS,
    CaptureError,
    DatasetCapture,
    encode,
)
from .types import CellDeletedError, CellSourceChangedError

if TYPE_CHECKING:
    from .session import NotebookSession

_NOTEBOOK_ID = "ajt_notebook_id"
_CELL_DATASET = "ajt_dataset"
_LOADER_NAMES = {"globals", "getattr", "ValueError", "__import__", "__builtins__"}


def _failure(error: CaptureError) -> dict[str, Any]:
    return {"ok": False, "error_code": str(error)}


def _plan(
    operation_id: str,
    name: str,
    columns: list[str],
    row_count: int,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    if not operation_id.strip() or len(operation_id) > 256:
        raise CaptureError("invalid_operation_id")
    if (
        not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name)
        or keyword.iskeyword(name)
        or name in _LOADER_NAMES
    ):
        raise CaptureError("invalid_table_name")
    if (
        len(columns) > MAX_COLUMNS
        or len(set(columns)) != len(columns)
        or any(not column or len(column) > 256 for column in columns)
        or not 0 <= row_count <= MAX_ROWS
        or (row_count and not columns)
    ):
        raise CaptureError("invalid_table_shape")
    try:
        payload = encode(metadata or {})
        encode(columns)
        encode(operation_id)
    except (TypeError, ValueError, RecursionError) as exc:
        raise CaptureError("invalid_table_data") from exc
    if len(payload) > MAX_METADATA_BYTES:
        raise CaptureError("metadata_too_large")
    return {
        "operation_id": operation_id,
        "name": name,
        "columns": columns,
        "row_count": row_count,
        "metadata": json.loads(payload),
    }


def _guarded_load(path: str, dataset_id: str, plan: dict[str, Any]) -> str:
    name, metadata = plan["name"], plan["metadata"]
    return (
        "# Load the saved notebook dataset.\n"
        f"if {name!r} not in globals():\n"
        f"    {name} = __import__('pandas').read_parquet({path!r}, engine='pyarrow')\n"
        f"    {name}.attrs['notebook_import'] = {metadata!r}\n"
        f"    {name}.attrs['ajt_dataset_id'] = {dataset_id!r}\n"
        f"elif getattr({name}, 'attrs', {{}}).get('ajt_dataset_id') != {dataset_id!r}:\n"
        "    raise ValueError('The dataset variable name is already in use.')\n"
    )


def _legacy_loader(path: str, dataset_id: str, plan: dict[str, Any]) -> str:
    code = _guarded_load(path, dataset_id, plan)
    query = plan["metadata"].get("query")
    if isinstance(query, str):
        query = query.replace("\x00", "\\0")
        code += "\n# Captured query\n" + "".join(f"# {line}\n" for line in query.splitlines())
    return code


def _preview(name: str) -> str:
    return f"{name}.head(5)\n"


def _loader(path: str, name: str) -> str:
    directory, filename = path[: -len(PureWindowsPath(path).name)], PureWindowsPath(path).name
    return (
        "import pandas as pd\n\n"
        f"{name} = pd.read_parquet(\n"
        f"    {directory!r}\n"
        f"    {filename!r},\n"
        "    engine='pyarrow',\n"
        ")\n\n" + _preview(name)
    )


def _query_cell(cells: list[dict[str, Any]], tag: str) -> str | None:
    return next(
        (
            cell["id"]
            for cell in cells
            if cell.get("cell_type") == "markdown"
            and tag in cell.get("metadata", {}).get("tags", [])
        ),
        None,
    )


class NotebookDatasets:
    """Manage dataset captures for open sessions on a private shared volume.

    ``root`` is writable by this process. ``kernel_root`` is the absolute
    path to that same storage from the kernel, if its mount differs.
    """

    def __init__(self, root: str | Path, kernel_root: str | None = None) -> None:
        if find_spec("pyarrow") is None or find_spec("filelock") is None:
            raise RuntimeError("Notebook datasets require the dataframe extra")
        from filelock import AsyncFileLock, Timeout

        self.root = Path(root).expanduser().resolve()
        location = str(self.root) if kernel_root is None else kernel_root
        mapping = PurePosixPath(location)
        self.kernel_root = mapping if mapping.is_absolute() else PureWindowsPath(location)
        if not self.kernel_root.is_absolute():
            raise ValueError("The kernel dataset root must be an absolute path")
        self.root.mkdir(mode=0o770, parents=True, exist_ok=True)
        self._async_lock = AsyncFileLock
        self._file_lock_timeout = Timeout

    async def _notebook_id(self, session: NotebookSession, *, create: bool = False) -> str:
        notebook_id = (await session.doc.get_metadata()).get(_NOTEBOOK_ID)
        if notebook_id is None and create:
            async with self._async_lock(self.root / ".identity.lock", timeout=30):
                notebook_id = (await session.doc.get_metadata()).get(_NOTEBOOK_ID)
                if notebook_id is None:
                    notebook_id = uuid.uuid4().hex
                    await session.doc.update_metadata({_NOTEBOOK_ID: notebook_id})
                await self._persist(session, metadata={_NOTEBOOK_ID: notebook_id})
        elif create:
            await self._persist(session, metadata={_NOTEBOOK_ID: notebook_id})
        if not isinstance(notebook_id, str) or not re.fullmatch(r"[a-f0-9]{32}", notebook_id):
            raise CaptureError("dataset_not_found")
        return notebook_id

    @staticmethod
    async def _persist(session: NotebookSession, **expected: Any) -> None:
        # File and Contents transports write synchronously. Collaboration may
        # need a server-side save before the notebook can survive a reconnect.
        wait = getattr(session.doc, "wait_until_persisted", None)
        if wait is not None:
            try:
                await wait(**expected)
            except (ClientError, OSError, RuntimeError) as exc:
                raise CaptureError("dataset_notebook_save_unconfirmed") from exc

    def _capture(self, notebook_id: str, dataset_id: str) -> DatasetCapture:
        if not re.fullmatch(r"[a-f0-9]{64}", dataset_id):
            raise CaptureError("invalid_dataset_id")
        directory = self.root / notebook_id
        if directory.resolve() != directory:
            raise CaptureError("invalid_dataset_path")
        return DatasetCapture(directory, dataset_id)

    async def begin(
        self,
        session: NotebookSession,
        *,
        operation_id: str,
        name: str,
        columns: list[str],
        row_count: int,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Initialize a capture or confirm the plan for an existing operation."""
        try:
            plan = _plan(operation_id, name, columns, row_count, metadata)
            notebook_id = await self._notebook_id(session, create=True)
            dataset_id = hashlib.sha256(encode([notebook_id, operation_id])).hexdigest()
            capture = self._capture(notebook_id, dataset_id)
            await asyncio.to_thread(capture.begin, plan)
        except CaptureError as exc:
            return _failure(exc)
        except (OSError, self._file_lock_timeout):
            return {"ok": False, "error_code": "dataset_storage_unavailable"}
        return {
            "ok": True,
            "dataset_id": dataset_id,
            "max_batch_bytes": MAX_BATCH_BYTES,
            "max_batches": MAX_BATCHES,
        }

    async def append(
        self, session: NotebookSession, *, dataset_id: str, index: int, rows: list[list[str | None]]
    ) -> dict[str, Any]:
        """Stage a bounded batch in column order, or confirm an identical retry."""
        try:
            capture = self._capture(await self._notebook_id(session), dataset_id)
            await asyncio.to_thread(capture.append, index, rows)
        except CaptureError as exc:
            return _failure(exc)
        except (OSError, self._file_lock_timeout):
            return {"ok": False, "error_code": "dataset_storage_unavailable"}
        return {"ok": True, "dataset_id": dataset_id, "index": index, "rows_written": len(rows)}

    async def commit(
        self, session: NotebookSession, *, dataset_id: str, batch_count: int
    ) -> dict[str, Any]:
        """Publish the snapshot and bind it through a single replayable loader cell."""
        try:
            notebook_id = await self._notebook_id(session)
            capture = self._capture(notebook_id, dataset_id)
            saved_plan, has_attrs = await asyncio.to_thread(capture.commit, batch_count)
            plan = _plan(**saved_plan)
            relative_path = capture.path.relative_to(self.root)
            async with self._async_lock(capture.directory / ".loader.lock", timeout=90):
                cell_id, query_cell_id = await self._load(
                    session, relative_path, dataset_id, plan, has_attrs
                )
        except CaptureError as exc:
            return {**_failure(exc), "dataset_id": dataset_id}
        except (OSError, self._file_lock_timeout):
            return {
                "ok": False,
                "error_code": "dataset_storage_unavailable",
                "dataset_id": dataset_id,
            }
        return {
            "ok": True,
            "dataset_id": dataset_id,
            "loader_cell_id": cell_id,
            **({"query_cell_id": query_cell_id} if query_cell_id else {}),
            "operation_id": plan["operation_id"],
            "table": plan["name"],
            "row_count": plan["row_count"],
            "column_count": len(plan["columns"]),
        }

    async def _load(
        self,
        session: NotebookSession,
        path: Path,
        dataset_id: str,
        plan: dict[str, Any],
        has_attrs: bool,
    ) -> tuple[str, str | None]:
        file = str(self.kernel_root.joinpath(*path.parts))
        legacy_source = _legacy_loader(file, dataset_id, plan)
        name = plan["name"]
        source = _loader(file, name) if has_attrs else legacy_source
        recent_loader = (
            source.removesuffix(_preview(name)) + f"{name}.info(verbose=False)\n" + _preview(name)
        )
        known_sources = (source, legacy_source, recent_loader) if has_attrs else (source,)
        guarded = _guarded_load(file, dataset_id, plan) + _preview(name) if has_attrs else source
        query_cell_id = None
        async with session._workflow_lock:
            document = await session.doc.fetch()
            for cell in document["cells"]:
                marker = cell.get("metadata", {}).get(_CELL_DATASET)
                if isinstance(marker, dict) and marker.get("dataset_id") == dataset_id:
                    cell_id = cell["id"]
                    cell_source = cell.get("source")
                    if isinstance(cell_source, list):
                        cell_source = "".join(cell_source)
                    if cell.get("cell_type") != "code" or cell_source not in known_sources:
                        raise CaptureError("dataset_loader_changed")
                    if cell_source != source:
                        try:
                            await session.doc.set_cell_source_by_id(
                                cell_id, source, expected_source=cell_source
                            )
                        except (CellDeletedError, CellSourceChangedError) as exc:
                            raise CaptureError("dataset_loader_changed") from exc
                    break
            else:
                _, cell_id = await session.doc.append_code_cell_with_id(
                    source,
                    metadata={
                        _CELL_DATASET: {
                            "dataset_id": dataset_id,
                            "path": path.as_posix(),
                            "format": "parquet",
                            "row_count": plan["row_count"],
                        }
                    },
                )
            query = plan["metadata"].get("query")
            if has_attrs and isinstance(query, str) and query.strip():
                tag = f"ajt-query-{dataset_id}"
                query_cell_id = _query_cell(document["cells"], tag)
                if query_cell_id is None:
                    index = await session.doc.resolve_cell_index(cell_id)
                    markdown = "## Captured query\n\n" + "\n".join(
                        f"    {line}" for line in query.splitlines()
                    )
                    await session.doc.insert_markdown_cell(index, markdown, tags=[tag])
                    query_cell_id = _query_cell((await session.doc.fetch())["cells"], tag)
                    if query_cell_id is None:
                        raise CaptureError("dataset_notebook_save_unconfirmed")
                await self._persist(session, cell_id=query_cell_id)
            try:
                cell_index = await session.doc.resolve_cell_index(cell_id)
                result = await session._execute_with_streaming(
                    source, cell_index, 45, cell_id=cell_id, execution_code=guarded
                )
            except KernelDisconnectedError as exc:
                raise CaptureError("dataset_load_unconfirmed") from exc
            await self._persist(session, cell_id=cell_id)
        if result.timed_out or result.outcome == "unknown" or result.persistence_status == "error":
            raise CaptureError("dataset_load_unconfirmed")
        if result.status != "ok":
            raise CaptureError("dataset_load_failed")
        return cell_id, query_cell_id
