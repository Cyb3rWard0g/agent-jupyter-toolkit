from __future__ import annotations

import pytest

from agent_jupyter_toolkit.kernel.history import decode_history_entries


def test_decode_input_only_history():
    [entry] = decode_history_entries([[4, 12, "value = 3"]])

    assert entry.session == 4
    assert entry.line_number == 12
    assert entry.input == "value = 3"
    assert entry.output is None


def test_decode_input_output_history():
    [entry] = decode_history_entries([[4, 13, ["value", "3"]]])

    assert entry.input == "value"
    assert entry.output == "3"


@pytest.mark.parametrize(
    "row",
    [
        [1, 2],
        [1, 2, ["input-only"]],
        ["one", 2, "input"],
        [1, 2, {"input": "value"}],
    ],
)
def test_reject_malformed_history_rows(row):
    with pytest.raises(ValueError):
        decode_history_entries([row])
