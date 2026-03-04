"""
Test DataFrame creation, manipulation, and export in kernel (no notebook).
"""

import os
from pathlib import Path

import pytest

from agent_jupyter_toolkit.kernel import SessionConfig, create_session

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
async def test_kernel_dataframe():
    out_csv = str(Path(__file__).parent / "data/test_kernel_output.csv")
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    sess = create_session(SessionConfig(mode="local", kernel_name="python3"))
    await sess.start()
    try:
        code = [
            "import pandas as pd",
            "df = pd.DataFrame({'a': [1, 3], 'b': [2, 4]})",
            f"df.to_csv(r'{out_csv}', index=False)",
            "del df",
        ]
        for c in code:
            result = await sess.execute(c)
            assert result.status == "ok", f"Kernel execution failed for {c!r}: {result.stderr}"
        assert os.path.exists(out_csv)
    finally:
        await sess.shutdown()
        if os.path.exists(out_csv):
            os.remove(out_csv)
