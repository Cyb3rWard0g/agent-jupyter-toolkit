"""Helpers shared by local and remote kernel history implementations."""

from __future__ import annotations

from typing import Any

from .types import HistoryEntry


def decode_history_entries(rows: list[Any]) -> list[HistoryEntry]:
    """Decode input-only and input/output Jupyter history rows."""
    entries: list[HistoryEntry] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise ValueError(f"Invalid history row: {row!r}")
        session, line_number, value = row
        output: str | None = None
        if isinstance(value, (list, tuple)):
            if len(value) != 2:
                raise ValueError(f"Invalid input/output history value: {value!r}")
            value, output = value
        if not isinstance(session, int) or not isinstance(line_number, int):
            raise ValueError(f"Invalid history coordinates: {row!r}")
        if not isinstance(value, str):
            raise ValueError(f"Invalid history input: {value!r}")
        if output is not None and not isinstance(output, str):
            output = str(output)
        entries.append(
            HistoryEntry(
                session=session,
                line_number=line_number,
                input=value,
                output=output,
            )
        )
    return entries
