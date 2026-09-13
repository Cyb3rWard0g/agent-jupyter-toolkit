"""Explicit notebook trust inspection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import nbformat
from nbformat.sign import NotebookNotary


@dataclass(frozen=True)
class NotebookTrustResult:
    """Trust status evaluated against one caller-supplied notary store."""

    signature_valid: bool
    cells_trusted: bool


def inspect_notebook_trust(
    notebook: dict[str, Any], *, notary: NotebookNotary
) -> NotebookTrustResult:
    """Inspect a notebook without signing it or choosing an implicit trust store."""
    node = nbformat.from_dict(notebook)
    return NotebookTrustResult(
        signature_valid=bool(notary.check_signature(node)),
        cells_trusted=bool(notary.check_cells(node)),
    )
