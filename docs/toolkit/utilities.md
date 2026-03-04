# Utilities

The `agent_jupyter_toolkit.utils` module provides high-level convenience
functions designed for AI agent workflows. These functions wrap the lower-level
kernel and notebook APIs with sensible defaults and structured error handling.

## Quick Factories

### `create_kernel()`

One-call kernel session creation:

```python
from agent_jupyter_toolkit.utils import create_kernel

# Local kernel
kernel = create_kernel("local", kernel_name="python3")

# Remote kernel
kernel = create_kernel(
    "remote",
    base_url="http://localhost:8888",
    token="YOUR_TOKEN",
    notebook_path="analysis.ipynb",  # optional: bind to notebook
)

async with kernel:
    result = await kernel.execute("1 + 1")
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mode` | `str` | `"local"` | `"local"` or `"remote"` |
| `kernel_name` | `str` | `"python3"` | Kernel spec name |
| `connection_file` | `str \| None` | `None` | Attach to existing kernel (local only) |
| `packer` | `str \| None` | `None` | Serializer for jupyter_client.Session |
| `base_url` | `str \| None` | `None` | Jupyter Server URL (remote only) |
| `token` | `str \| None` | `None` | API token (remote only) |
| `notebook_path` | `str \| None` | `None` | Bind to notebook (remote only) |
| `headers` | `dict \| None` | `None` | Extra HTTP headers (remote only) |

### `create_notebook_transport()`

One-call notebook transport creation:

```python
from agent_jupyter_toolkit.utils import create_notebook_transport

# Local file
doc = create_notebook_transport("local", "analysis.ipynb")

# Remote (Contents API)
doc = create_notebook_transport(
    "remote", "analysis.ipynb",
    base_url="http://localhost:8888",
    token="YOUR_TOKEN",
)

# Collaborative (Yjs/CRDT)
doc = create_notebook_transport(
    "remote", "shared.ipynb",
    base_url="http://localhost:8888",
    prefer_collab=True,
)
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mode` | `str` | — | `"local"` or `"remote"` |
| `path` | `str` | — | Notebook path |
| `base_url` | `str \| None` | `None` | Server URL (remote only) |
| `token` | `str \| None` | `None` | API token (remote only) |
| `headers` | `dict \| None` | `None` | Extra headers (remote only) |
| `prefer_collab` | `bool` | `False` | Use Yjs transport if available |
| `create_if_missing` | `bool` | `True` | Create notebook if absent |
| `local_autosave_delay` | `float \| None` | `None` | Debounce delay in seconds |

## Execution Helpers

### `execute_code()`

Execute code and get a fully processed result:

```python
from agent_jupyter_toolkit.utils import execute_code

result = await execute_code(kernel, "sum(range(100))", timeout=30.0)

print(result.status)            # "ok"
print(result.formatted_output)  # "4950"
print(result.text_outputs)      # ["4950"]
print(result.error_message)     # None (or error text)
print(result.elapsed_seconds)   # 0.05
```

Returns `NotebookCodeExecutionResult` with `cell_index=-1` (indicates direct
kernel execution without a notebook).

### `invoke_code_cell()`

Execute code in a new notebook cell:

```python
from agent_jupyter_toolkit.utils import invoke_code_cell

result = await invoke_code_cell(notebook_session, "df.describe()", timeout=60.0)

print(result.cell_index)        # 3 (index of appended cell)
print(result.formatted_output)  # formatted DataFrame output
```

### `invoke_existing_cell()`

Re-execute an existing cell with updated code:

```python
from agent_jupyter_toolkit.utils import invoke_existing_cell

result = await invoke_existing_cell(
    notebook_session, index=2, code="df.head(10)", timeout=60.0
)
```

### `invoke_markdown_cell()`

Add a markdown cell. Pass `index` to insert at a specific position (0-based); omit it to append.

```python
from agent_jupyter_toolkit.utils import invoke_markdown_cell

# Append to end
result = await invoke_markdown_cell(notebook_session, "# Analysis Results")
print(result.cell_index)  # index of the new cell

# Insert at the beginning
result = await invoke_markdown_cell(notebook_session, "# Title", index=0)
```

### `invoke_notebook_cells()`

Execute multiple cells in sequence:

```python
from agent_jupyter_toolkit.utils import invoke_notebook_cells

cells = [
    "import pandas as pd",
    "df = pd.read_csv('data.csv')",
    "df.describe()",
]
results = await invoke_notebook_cells(notebook_session, cells, timeout=120.0)
for r in results:
    print(f"Cell {r.cell_index}: {r.status}")
```

### `get_session_info()`

Get information about the current kernel/notebook session:

```python
from agent_jupyter_toolkit.utils import get_session_info

info = await get_session_info(notebook_session)
```

### `get_variables()` / `get_variable_value()`

Shorthand for variable inspection:

```python
from agent_jupyter_toolkit.utils import get_variables, get_variable_value

variables = await get_variables(kernel)
value = await get_variable_value(kernel, "my_var")
```

## Output Helpers

### `extract_outputs()`

Extract readable text from nbformat output dicts:

```python
from agent_jupyter_toolkit.utils import extract_outputs

texts = extract_outputs(result.outputs)
# → ["4950"]  (list of human-readable strings)
```

### `format_output()`

Format all outputs into a single string:

```python
from agent_jupyter_toolkit.utils import format_output

text = format_output(result.outputs)
# → "4950"

# With type prefixes
text = format_output(result.outputs, include_types=True)
# → "[result] 4950"

# With length limit
text = format_output(result.outputs, max_length=500)
```

### `get_result_value()`

Extract the primary `execute_result` value:

```python
from agent_jupyter_toolkit.utils import get_result_value

value = get_result_value(result.outputs)
# → "4950" (string) or None
```

## Package Management

Manage pip packages in the kernel environment:

### `check_package_availability()`

```python
from agent_jupyter_toolkit.utils import check_package_availability

status = await check_package_availability(kernel, ["pandas", "numpy", "plotly"])
# → {"pandas": True, "numpy": True, "plotly": False}
```

Uses `importlib.metadata` for accurate distribution-level checks.

### `ensure_packages()`

Install missing packages and return overall success:

```python
from agent_jupyter_toolkit.utils import ensure_packages

success = await ensure_packages(kernel, ["pandas", "plotly", "seaborn"])
# → True (all installed or already present)
```

### `install_package()`

Install a single package:

```python
from agent_jupyter_toolkit.utils import install_package

ok = await install_package(kernel, "beautifulsoup4")
```

### `update_dependencies()`

Check and install missing dependencies:

```python
from agent_jupyter_toolkit.utils import update_dependencies

ok = await update_dependencies(kernel, ["pandas>=2.0", "numpy"])
```

### `ensure_packages_with_report()`

Install missing packages and return a detailed per-package report:

```python
from agent_jupyter_toolkit.utils import ensure_packages_with_report

result = await ensure_packages_with_report(kernel, ["pandas", "plotly", "seaborn"])
# → {
#     "success": True,
#     "report": {
#         "pandas": {"already_available": True, "installed": False, "failed": False},
#         "plotly": {"already_available": False, "installed": True, "failed": False},
#         "seaborn": {"already_available": False, "installed": True, "failed": False},
#     }
# }
```

Returns a dict with `"success"` (overall bool) and `"report"` (per-package
status). Each package entry includes `already_available`, `installed`, and
`failed` flags. This is the recommended function for agent workflows because
it gives full visibility into what happened with each package.

### Pre-defined package groups

```python
from agent_jupyter_toolkit.utils import (
    SCIENTIFIC_PACKAGES,   # numpy, scipy, pandas, matplotlib, ...
    ML_PACKAGES,           # scikit-learn, tensorflow, torch, ...
    DATA_VIZ_PACKAGES,     # plotly, seaborn, bokeh, ...
    WEB_PACKAGES,          # requests, beautifulsoup4, aiohttp, ...
)

await ensure_packages(kernel, SCIENTIFIC_PACKAGES)
```

## Notebook File Helpers

### `create_minimal_notebook_content()`

```python
from agent_jupyter_toolkit.utils import create_minimal_notebook_content

content = create_minimal_notebook_content()
# → {"cells": [], "metadata": {...}, "nbformat": 4, "nbformat_minor": 5}
```

### `create_notebook_via_contents_api()`

Create a notebook on a remote Jupyter Server:

```python
from agent_jupyter_toolkit.utils import create_notebook_via_contents_api

await create_notebook_via_contents_api(
    base_url="http://localhost:8888",
    path="notebooks/new.ipynb",
    token="YOUR_TOKEN",
)
```
