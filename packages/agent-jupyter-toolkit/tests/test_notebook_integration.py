import os
from pathlib import Path

import nbformat
import pytest

from agent_jupyter_toolkit.kernel import SessionConfig, create_session
from agent_jupyter_toolkit.notebook.cells import create_code_cell, create_markdown_cell
from agent_jupyter_toolkit.notebook.utils import (
    ensure_allowed_for_write,
    load_notebook,
    save_notebook,
    to_nbformat_outputs,
    validate_notebook,
)

pytestmark = pytest.mark.asyncio


def _has_working_pandas() -> bool:
    try:
        import pandas  # noqa: F401
    except Exception:
        return False
    return True


skip_pandas = pytest.mark.skipif(
    not _has_working_pandas(),
    reason="pandas is unavailable or ABI-incompatible in this environment",
)


@skip_pandas
async def test_notebook_integration():
    nb_path = ensure_allowed_for_write(Path(__file__).parent / "notebook_integration_test.ipynb")
    nb = nbformat.v4.new_notebook()
    # Add markdown cell
    nb.cells.append(
        create_markdown_cell(
            [
                "# Notebook Integration Test",
                "Test DataFrame creation, inspection, and export using notebook and kernel.",
            ]
        )
    )
    out_csv = str(Path(__file__).parent / "data/notebook_integration_output.csv")
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    code_cells = [
        "import pandas as pd",
        "df = pd.DataFrame({'a': [1, 3], 'b': [2, 4]})",
        "df",
        "print(df.shape[0], df.shape[1])",
        f"df.to_csv(r'{out_csv}', index=False)",
        "del df",
    ]
    for code in code_cells:
        nb.cells.append(create_code_cell([code]))
    save_notebook(nb, nb_path)
    sess = create_session(SessionConfig(mode="local", kernel_name="python3"))
    await sess.start()
    try:
        for idx, code in enumerate(code_cells):
            result = await sess.execute(code)
            assert result.status == "ok"
            nb = load_notebook(nb_path)
            outputs = to_nbformat_outputs(result)
            nb.cells[idx + 1]["outputs"] = outputs
            nb.cells[idx + 1]["execution_count"] = result.execution_count
            save_notebook(nb, nb_path)
        assert os.path.exists(out_csv)
        validate_notebook(nb)
    finally:
        await sess.shutdown()
        if os.path.exists(out_csv):
            os.remove(out_csv)
        if os.path.exists(nb_path):
            os.remove(nb_path)
