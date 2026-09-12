"""Incremental Jupyter execution output state shared by kernel transports."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from .types import ExecutionResult


class ExecutionState:
    """Apply kernel messages while preserving Notebook-compatible output semantics."""

    def __init__(self, *, max_output_bytes: int | None = 50 * 1024 * 1024) -> None:
        self.outputs: list[dict[str, Any]] = []
        self.execution_count: int | None = None
        self.status = "ok"
        self.user_expressions: dict[str, Any] | None = None
        self._pending_clear = False
        self._display_indices: dict[str, set[int]] = {}
        self.display_updates: dict[str, dict[str, Any]] = {}
        self.max_output_bytes = max_output_bytes
        self.output_truncated = False
        self.dropped_output_bytes = 0
        self._output_bytes = 0
        self._display_update_bytes = 0

    def apply(self, message: dict[str, Any]) -> bool:
        """Apply one message and return whether visible execution state changed."""
        msg_type = message.get("msg_type") or (message.get("header") or {}).get("msg_type")
        content = message.get("content") or {}
        if not msg_type:
            return False

        if msg_type == "execute_input":
            self.execution_count = content.get("execution_count")
            return True
        if msg_type == "clear_output":
            if content.get("wait", False):
                self._pending_clear = True
            else:
                self._clear_visible()
            return True
        if msg_type == "stream":
            self._clear_if_pending()
            name = content.get("name")
            text = content.get("text", "") or ""
            text = self._fit_text(str(text))
            if not text:
                return self.output_truncated
            if (
                self.outputs
                and self.outputs[-1].get("output_type") == "stream"
                and self.outputs[-1].get("name") == name
            ):
                self.outputs[-1]["text"] += text
            else:
                self.outputs.append({"output_type": "stream", "name": name, "text": text})
            return True
        if msg_type in {"display_data", "execute_result"}:
            self._clear_if_pending()
            output: dict[str, Any] = {
                "output_type": msg_type,
                "data": deepcopy(content.get("data") or {}),
                "metadata": deepcopy(content.get("metadata") or {}),
            }
            if msg_type == "execute_result":
                count = content.get("execution_count")
                if count is not None:
                    self.execution_count = count
                output["execution_count"] = count
            if not self._reserve_output(output):
                return True
            self.outputs.append(output)
            display_id = (content.get("transient") or {}).get("display_id")
            if display_id:
                self._display_indices.setdefault(display_id, set()).add(len(self.outputs) - 1)
            return True
        if msg_type == "update_display_data":
            display_id = (content.get("transient") or {}).get("display_id")
            if not display_id:
                return False
            update = {
                "data": content.get("data") or {},
                "metadata": content.get("metadata") or {},
            }
            previous = self.display_updates.get(display_id)
            update_delta = self._measure({display_id: update}) - (
                self._measure({display_id: previous}) if previous is not None else 0
            )
            delta = update_delta
            replacements = {}
            for index in sorted(self._display_indices.get(display_id, ())):
                if index >= len(self.outputs):
                    continue
                replacement = dict(self.outputs[index])
                replacement.update(update)
                delta += self._measure(replacement) - self._measure(self.outputs[index])
                replacements[index] = replacement
            # Sidecars can target earlier cells, so they share the visible-output
            # budget. Accept the update and all local replacements together.
            if not self._reserve_bytes(max(0, delta)):
                return True
            self._output_bytes += min(0, delta)
            self._display_update_bytes += update_delta
            self.display_updates[display_id] = deepcopy(update)
            for index, replacement in replacements.items():
                self.outputs[index] = deepcopy(replacement)
            return True
        if msg_type == "error":
            self._clear_if_pending()
            self.status = "error"
            output = {
                "output_type": "error",
                "ename": content.get("ename"),
                "evalue": content.get("evalue"),
                "traceback": deepcopy(content.get("traceback") or []),
            }
            if self._reserve_output(output):
                self.outputs.append(output)
            return True
        if msg_type == "execute_reply":
            self.status = content.get("status", self.status)
            count = content.get("execution_count")
            if count is not None:
                self.execution_count = count
            expressions = content.get("user_expressions")
            if isinstance(expressions, dict):
                self.user_expressions = deepcopy(expressions)
            return True
        return False

    def snapshot(self) -> tuple[list[dict[str, Any]], int | None]:
        """Return an isolated visible-output snapshot for callbacks."""
        return deepcopy(self.outputs), self.execution_count

    def result(self) -> ExecutionResult:
        """Build the public result from the current state."""
        stdout = "".join(
            output.get("text", "")
            for output in self.outputs
            if output.get("output_type") == "stream" and output.get("name") == "stdout"
        )
        stderr = "".join(
            output.get("text", "")
            for output in self.outputs
            if output.get("output_type") == "stream" and output.get("name") == "stderr"
        )
        return ExecutionResult(
            status=self.status,
            execution_count=self.execution_count,
            stdout=stdout,
            stderr=stderr,
            outputs=deepcopy(self.outputs),
            user_expressions=deepcopy(self.user_expressions),
            output_truncated=self.output_truncated,
            dropped_output_bytes=self.dropped_output_bytes,
            display_ids={
                display_id: sorted(indices) for display_id, indices in self._display_indices.items()
            },
            display_updates=deepcopy(self.display_updates),
        )

    def _clear_if_pending(self) -> None:
        if self._pending_clear:
            self._clear_visible()

    def _clear_visible(self) -> None:
        self.outputs.clear()
        self._display_indices.clear()
        self._pending_clear = False
        self._output_bytes = self._display_update_bytes

    def _fit_text(self, value: str) -> str:
        encoded = value.encode("utf-8")
        if self.max_output_bytes is None:
            self._output_bytes += len(encoded)
            return value
        available = max(0, self.max_output_bytes - self._output_bytes)
        kept = encoded[:available]
        while kept:
            try:
                result = kept.decode("utf-8")
                break
            except UnicodeDecodeError:
                kept = kept[:-1]
        else:
            result = ""
        dropped = len(encoded) - len(kept)
        self._output_bytes += len(kept)
        if dropped:
            self.output_truncated = True
            self.dropped_output_bytes += dropped
        return result

    def _reserve_output(self, output: dict[str, Any]) -> bool:
        return self._reserve_bytes(self._measure(output))

    def _reserve_bytes(self, size: int) -> bool:
        if self.max_output_bytes is not None and self._output_bytes + size > self.max_output_bytes:
            self.output_truncated = True
            self.dropped_output_bytes += size
            return False
        self._output_bytes += size
        return True

    @staticmethod
    def _measure(output: dict[str, Any]) -> int:
        return len(json.dumps(output, default=str, ensure_ascii=False).encode("utf-8"))
