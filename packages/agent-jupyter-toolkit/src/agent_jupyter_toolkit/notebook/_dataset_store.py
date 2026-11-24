"""Bounded staging and atomic publication for notebook Parquet captures."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

MAX_BATCH_BYTES = 512 * 1024
MAX_BATCHES = 128
MAX_COLUMNS = 256
MAX_ROWS = 10_000
MAX_METADATA_BYTES = 64 * 1024
_FOOTER_KEY = b"ajt_capture_v1"
_PANDAS_ATTRS = b"PANDAS_ATTRS"


class CaptureError(Exception):
    """A dataset error that is safe to return to a caller as a code."""


def encode(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


@contextmanager
def _atomic_file(target: Path, *, mode: int = 0o600):
    with NamedTemporaryFile(dir=target.parent, prefix=".capture-", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        yield temporary
        temporary.chmod(mode)
        with temporary.open("rb+") as handle:
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


class DatasetCapture:
    """One immutable published file with temporary numbered batches before commit.

    Its file lock lives on the shared volume and is acquired in the worker
    thread so cancellation of the caller cannot interrupt a file mutation.
    """

    def __init__(self, directory: Path, dataset_id: str) -> None:
        self.directory = directory
        self.dataset_id = dataset_id
        self.path = directory / f"{dataset_id}.parquet"
        self.pending = directory / ".pending" / dataset_id
        self.plan_path = self.pending / "plan.json"

    def _lock(self):
        from filelock import FileLock

        return FileLock(self.directory / f".{self.dataset_id}.lock", timeout=30)

    def _state(self) -> tuple[dict[str, Any], list[str] | None]:
        if self.path.is_file():
            from pyarrow.parquet import read_metadata

            footer = read_metadata(self.path).metadata or {}
            if _FOOTER_KEY not in footer:
                raise CaptureError("invalid_dataset")
            state = json.loads(footer[_FOOTER_KEY])
            return state["plan"], state["digests"]
        if self.plan_path.is_file():
            return json.loads(self.plan_path.read_bytes()), None
        raise CaptureError("dataset_not_found")

    def begin(self, plan: dict[str, Any]) -> None:
        self.directory.mkdir(mode=0o770, parents=True, exist_ok=True)
        with self._lock():
            if self.path.exists() or self.plan_path.exists():
                if self._state()[0] != plan:
                    raise CaptureError("operation_conflict")
                return
            self.pending.mkdir(mode=0o700, parents=True, exist_ok=True)
            with _atomic_file(self.plan_path) as temporary:
                temporary.write_bytes(encode(plan))

    def _batch(self, index: int) -> Path:
        return self.pending / f"{index}.json"

    def append(self, index: int, rows: list[list[str | None]]) -> None:
        if not 0 <= index < MAX_BATCHES or not 1 <= len(rows) <= MAX_ROWS:
            raise CaptureError("invalid_batch")
        try:
            payload = encode(rows)
        except (TypeError, ValueError, RecursionError) as exc:
            raise CaptureError("invalid_batch") from exc
        if len(payload) > MAX_BATCH_BYTES:
            raise CaptureError("batch_too_large")

        with self._lock():
            plan, digests = self._state()
            if not plan["columns"] or any(
                len(row) != len(plan["columns"])
                or any(value is not None and not isinstance(value, str) for value in row)
                for row in rows
            ):
                raise CaptureError("invalid_batch")
            if digests is not None:
                if index >= len(digests) or digests[index] != hashlib.sha256(payload).hexdigest():
                    raise CaptureError("operation_conflict")
                return
            if len(rows) > plan["row_count"] or index >= plan["row_count"]:
                raise CaptureError("invalid_batch")
            batch = self._batch(index)
            if batch.is_file():
                if batch.read_bytes() != payload:
                    raise CaptureError("operation_conflict")
                return
            if index and not self._batch(index - 1).is_file():
                raise CaptureError("batch_out_of_order")
            with _atomic_file(batch) as temporary:
                temporary.write_bytes(payload)

    def commit(self, batch_count: int) -> tuple[dict[str, Any], bool]:
        from pyarrow.parquet import read_schema

        if not 0 <= batch_count <= MAX_BATCHES:
            raise CaptureError("invalid_batch_count")
        with self._lock():
            plan, digests = self._state()
            if digests is None:
                self._publish(plan, batch_count)
            elif len(digests) != batch_count:
                raise CaptureError("operation_conflict")
            if self.pending.is_dir():
                shutil.rmtree(self.pending)
            schema_metadata = read_schema(self.path).metadata or {}
            return plan, _PANDAS_ATTRS in schema_metadata

    def _publish(self, plan: dict[str, Any], batch_count: int) -> None:
        import pyarrow as pa
        from pyarrow.parquet import ParquetWriter

        if self._batch(batch_count).exists():
            raise CaptureError("invalid_batch_count")
        attrs = {"notebook_import": plan["metadata"], "ajt_dataset_id": self.dataset_id}
        schema = pa.schema(
            [(column, pa.string()) for column in plan["columns"]],
            metadata={_PANDAS_ATTRS: encode(attrs)},
        )
        digests: list[str] = []
        row_count = 0
        with (
            _atomic_file(self.path, mode=0o640) as temporary,
            ParquetWriter(temporary, schema, compression="zstd") as writer,
        ):
            for index in range(batch_count):
                try:
                    payload = self._batch(index).read_bytes()
                except FileNotFoundError as exc:
                    raise CaptureError("dataset_incomplete") from exc
                rows = json.loads(payload)
                row_count += len(rows)
                if row_count > plan["row_count"]:
                    raise CaptureError("dataset_row_count_mismatch")
                arrays = [pa.array(values, type=pa.string()) for values in zip(*rows, strict=True)]
                writer.write_batch(pa.RecordBatch.from_arrays(arrays, schema=schema))
                digests.append(hashlib.sha256(payload).hexdigest())
            if row_count != plan["row_count"]:
                raise CaptureError("dataset_incomplete")
            writer.add_key_value_metadata({_FOOTER_KEY: encode({"plan": plan, "digests": digests})})
