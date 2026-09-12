# Configuration

`agent-jupyter-toolkit` uses environment variables and dataclass-based
configuration for runtime behavior.

## Kernel Configuration

Kernel sessions are configured via `SessionConfig` and `ServerConfig` dataclasses
passed to `create_session()`:

### `SessionConfig`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | `str` | `"local"` | `"local"` or `"server"` |
| `kernel_name` | `str` | `"python3"` | Kernel spec to spawn |
| `connection_file_name` | `str \| None` | `None` | Attach to existing kernel (local mode) |
| `packer` | `str \| None` | `None` | Serializer name (`"json"`, `"orjson"`) |
| `startup_timeout` | `float` | `60.0` | Seconds to wait for local kernel readiness |
| `cwd` | `str \| None` | `None` | Working directory for a newly launched local kernel |
| `env` | `dict[str, str] \| None` | `None` | Environment overrides merged into the launch environment |
| `kernel_args` | `list[str]` | `[]` | Additional kernelspec launch arguments |
| `max_output_bytes` | `int \| None` | `52428800` | Visible output budget; `None` disables the limit |
| `output_callback_timeout` | `float \| None` | `30.0` | Per-callback delivery limit; `None` disables it |
| `transport_encryption` | `str` | `"disabled"` | CurveZMQ policy: `disabled`, `auto`, or `required` |
| `manager_factory` | `Callable \| None` | `None` | Factory implementing the toolkit's `KernelManager` interface |
| `server` | `ServerConfig \| None` | `None` | Required when `mode="server"` |

When `connection_file_name` is set, it must identify an existing file. A missing
file raises `FileNotFoundError` and never launches a replacement kernel. `cwd`,
`env`, `kernel_args`, and `transport_encryption` are launch-only options and are
rejected in attachment mode.

### `ServerConfig`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `base_url` | `str` | — | Jupyter Server URL (e.g., `http://localhost:8888`) |
| `token` | `str \| None` | `None` | API token for `Authorization: Token <token>` |
| `headers` | `dict \| None` | `None` | Extra HTTP headers (cookies, XSRF, etc.) |
| `kernel_name` | `str` | `"python3"` | Kernel to create on the server |
| `notebook_path` | `str \| None` | `None` | Bind kernel to a specific notebook via Sessions API |
| `request_timeout` | `float` | `30.0` | Timeout for server control requests |
| `startup_timeout` | `float` | `60.0` | Timeout for restart/readiness checks |
| `max_output_bytes` | `int \| None` | `52428800` | Visible output budget; `None` disables the limit |
| `output_callback_timeout` | `float \| None` | `30.0` | Per-callback delivery limit; `None` disables it |

### Example

```python
from agent_jupyter_toolkit.kernel import create_session, SessionConfig, ServerConfig

# Local with custom kernel
config = SessionConfig(mode="local", kernel_name="julia-1.9")

# Remote with full configuration
config = SessionConfig(
    mode="server",
    server=ServerConfig(
        base_url="https://hub.example.com/user/alex",
        token="abc123",
        headers={"Cookie": "session=xyz"},
        kernel_name="python3",
        notebook_path="project/analysis.ipynb",
    ),
)
```

## Notebook Environment Variables

The notebook subsystem reads several environment variables at import time
via the `Config` dataclass in `notebook.config`:

### `JAT_NOTEBOOK_ALLOWLIST`

Comma-separated list of allowed filesystem paths for local notebook
operations. Paths are resolved to absolute form.

```bash
export JAT_NOTEBOOK_ALLOWLIST="/home/user/notebooks,/tmp/scratch"
```

- If unset, defaults to the **current working directory**
- Both files and directories can be specified
- Non-existent paths are allowed (for files that will be created)
- All local file transport operations validate paths against this list
- Paths outside the allowlist raise `PermissionError`

### `JAT_NOTEBOOK_TIMEOUT`

Default execution timeout in seconds for notebook operations:

```bash
export JAT_NOTEBOOK_TIMEOUT=30
```

- Default: `20` seconds
- Must be a positive integer
- Invalid values fall back to the default

### `JAT_NOTEBOOK_KERNEL_PREWARM`

Python code executed automatically when a kernel starts to warm up common
imports:

```bash
export JAT_NOTEBOOK_KERNEL_PREWARM="import pandas as pd; import numpy as np"
```

- Default: `"import pandas as pd; import numpy as np; import json, os, sys"`
- Set to empty string to disable prewarming
- Only runs for Python kernels

## Notebook Transport Configuration

The `make_document_transport()` factory and `create_notebook_transport()` utility
accept these parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `mode` | `str` | `"local"` or `"server"` |
| `local_path` | `str` | Filesystem path for local mode |
| `remote_base` | `str` | Server URL for remote mode |
| `remote_path` | `str` | Notebook path on server |
| `token` | `str` | Authentication token |
| `headers_json` | `str` | JSON string of extra headers |
| `prefer_collab` | `bool` | Use Yjs/CRDT transport if available |
| `collaboration_mode` | `str \| None` | `required`, `preferred`, or `disabled`; overrides `prefer_collab` |
| `create_if_missing` | `bool` | Create notebook if it doesn't exist |
| `local_autosave_delay` | `float` | Debounce delay for local writes (seconds) |

## Logging

The toolkit uses standard Python `logging` throughout. To enable debug output:

```python
import logging

# All toolkit logging
logging.getLogger("agent_jupyter_toolkit").setLevel(logging.DEBUG)

# Just kernel operations
logging.getLogger("agent_jupyter_toolkit.kernel").setLevel(logging.DEBUG)

# Just notebook operations
logging.getLogger("agent_jupyter_toolkit.notebook").setLevel(logging.DEBUG)
```

Key logger names:

| Logger | Covers |
|--------|--------|
| `agent_jupyter_toolkit.kernel.session` | Session lifecycle |
| `agent_jupyter_toolkit.kernel.transports.local` | ZMQ transport messages |
| `agent_jupyter_toolkit.kernel.transports.server` | HTTP/WS transport |
| `agent_jupyter_toolkit.kernel.manager` | Kernel process management |
| `agent_jupyter_toolkit.notebook.session` | Notebook session orchestration |
| `agent_jupyter_toolkit.notebook.transports.local_file` | Local file I/O |
| `agent_jupyter_toolkit.notebook.transports.contents` | Contents API requests |
| `agent_jupyter_toolkit.notebook.transports.collab` | Yjs/CRDT sync |

## Security Considerations

### Path allowlisting

The local file transport validates all paths against `JAT_NOTEBOOK_ALLOWLIST`.
This prevents agents from reading/writing arbitrary files:

```python
from agent_jupyter_toolkit.notebook.utils import ensure_allowed

# OK — within CWD (default allowlist)
path = ensure_allowed(Path("notebooks/analysis.ipynb"))

# PermissionError — outside allowlist
path = ensure_allowed(Path("/etc/passwd"))
```

### Variable injection prevention

`VariableManager.set()` serializes values via base64-encoded JSON to prevent
code injection through crafted variable names or values. Variable names are
validated as legal Python identifiers. Non-JSON values, non-finite numbers, and
kernel-side assignment errors are rejected instead of falling back to `repr()`.

### Pickle warnings

The MIME type serialization system uses `pickle` as a last-resort fallback.
This logs a warning because deserialization of untrusted pickle data is a
security risk. Prefer JSON-serializable types or register custom handlers.
