# API Reference

Complete reference for all public classes, functions, and types in
`agent-jupyter-toolkit`.

## `agent_jupyter_toolkit.kernel`

### `create_session(config=None)`

Create a `Session` with automatic transport selection.

```python
def create_session(config: SessionConfig | None = None) -> Session
```

- `config=None` defaults to `SessionConfig()` (local, python3)
- `mode="local"` → `LocalTransport`
- `mode="server"` → `ServerTransport` (requires `config.server`)
- Raises `ValueError` if server mode without `ServerConfig`

---

### `Session`

High-level kernel session wrapping a `KernelTransport`.

#### Lifecycle

| Method | Signature | Description |
|--------|-----------|-------------|
| `start()` | `async def start() -> None` | Start/attach to kernel |
| `shutdown()` | `async def shutdown() -> None` | Stop kernel, release resources |
| `is_alive()` | `async def is_alive() -> bool` | Kernel responsiveness check |

Supports `async with` for automatic lifecycle.

#### Execution

```python
async def execute(
    self,
    code: str,
    *,
    timeout: float | None = None,
    output_callback: OutputCallback | None = None,
    store_history: bool = True,
    allow_stdin: bool = False,
    stop_on_error: bool = True,
) -> ExecutionResult
```

#### Introspection

| Method | Signature | Returns |
|--------|-----------|---------|
| `complete(code, cursor_pos)` | `async def complete(code: str, cursor_pos: int) -> CompleteResult` | Tab-completion matches |
| `inspect(code, cursor_pos, detail_level=0)` | `async def inspect(code: str, cursor_pos: int, detail_level: int = 0) -> InspectResult` | Object documentation |
| `is_complete(code)` | `async def is_complete(code: str) -> IsCompleteResult` | Syntax completeness |
| `history(output=False, raw=True, hist_access_type="tail", n=10)` | `async def history(...) -> HistoryResult` | Execution history |
| `kernel_info()` | `async def kernel_info() -> KernelInfoResult` | Kernel metadata |

#### Control

| Method | Signature | Description |
|--------|-----------|-------------|
| `interrupt()` | `async def interrupt() -> None` | Send SIGINT to kernel |

#### Properties

| Property | Type | Description |
|----------|------|-------------|
| `kernel_manager` | `KernelManager \| None` | Underlying manager (local only) |

---

### `SessionConfig`

```python
@dataclass
class SessionConfig:
    mode: str = "local"
    kernel_name: str = "python3"
    connection_file_name: str | None = None
    packer: str | None = None
    server: ServerConfig | None = None
```

### `ServerConfig`

```python
@dataclass
class ServerConfig:
    base_url: str
    token: str | None = None
    headers: dict[str, str] | None = None
    kernel_name: str = "python3"
    notebook_path: str | None = None
```

### `ExecutionResult`

```python
@dataclass
class ExecutionResult:
    status: str = "ok"                              # "ok" or "error"
    execution_count: int | None = None
    stdout: str = ""
    stderr: str = ""
    outputs: list[dict[str, Any]] = field(...)      # nbformat output dicts
    user_expressions: dict[str, Any] | None = None
    elapsed_ms: float | None = None
```

### `CompleteResult`

```python
@dataclass
class CompleteResult:
    matches: list[str] = field(...)
    cursor_start: int = 0
    cursor_end: int = 0
    status: str = "ok"
    metadata: dict[str, Any] = field(...)
```

### `InspectResult`

```python
@dataclass
class InspectResult:
    found: bool = False
    data: dict[str, str] = field(...)       # {"text/plain": "..."}
    metadata: dict[str, Any] = field(...)
    status: str = "ok"
```

### `IsCompleteResult`

```python
@dataclass
class IsCompleteResult:
    status: str = "unknown"     # "complete", "incomplete", "invalid", "unknown"
    indent: str = ""            # suggested indent for "incomplete"
```

### `HistoryResult`

```python
@dataclass
class HistoryResult:
    history: list[HistoryEntry] = field(...)
    status: str = "ok"
```

### `HistoryEntry`

```python
@dataclass
class HistoryEntry:
    session: int
    line_number: int
    input: str
    output: str | None = None
```

### `KernelInfoResult`

```python
@dataclass
class KernelInfoResult:
    protocol_version: str = ""
    implementation: str = ""
    implementation_version: str = ""
    language_info: dict[str, Any] = field(...)
    banner: str = ""
    status: str = "ok"
```

### `OutputCallback`

```python
OutputCallback = Callable[[list[dict[str, Any]], int | None], Awaitable[None]]
```

### Exceptions

| Exception | Parent | Description |
|-----------|--------|-------------|
| `KernelError` | `Exception` | Base for all kernel errors |
| `KernelExecutionError` | `KernelError` | Code execution failure |
| `KernelTimeoutError` | `KernelError` | Operation timeout |

---

### `KernelTransport`

Base class for kernel transport implementations. All methods raise
`NotImplementedError` by default (except `start`, `shutdown`, `is_alive`,
`execute` which are no-ops).

Implementations: `LocalTransport`, `ServerTransport`.

---

### `KernelManager`

Low-level kernel process manager (wraps `AsyncKernelManager`).

| Method | Description |
|--------|-------------|
| `start()` | Spawn kernel, open channels, wait for ready |
| `connect_to_existing(connection_file)` | Attach to running kernel |
| `shutdown()` | Stop channels and kernel process |
| `restart()` | Restart kernel and reopen channels |
| `interrupt()` | Send SIGINT to kernel process |
| `is_alive()` | Process alive check |
| `is_healthy()` | kernel_info round-trip check |

| Property | Type |
|----------|------|
| `client` | `AsyncKernelClient \| None` |
| `connection_file_path` | `str \| None` |
| `shell_channel` | ZMQ channel |
| `iopub_channel` | ZMQ channel |
| `stdin_channel` | ZMQ channel |
| `control_channel` | ZMQ channel |
| `hb_channel` | ZMQ channel |

---

### `VariableManager`

```python
class VariableManager:
    def __init__(self, session: Session, language: str = "python"): ...
    async def set(self, name: str, value: Any, mimetype=None) -> None: ...
    async def get(self, name: str) -> Any: ...
    async def list(self, *, detailed: bool = False) -> list[str] | list[VariableDescription]: ...
```

---

### `KernelHooks`

```python
class KernelHooks:
    def register_output_hook(self, hook: Callable[[dict], None]) -> None: ...
    def unregister_output_hook(self, hook: Callable[[dict], None]) -> None: ...
    def trigger_output_hooks(self, msg: dict) -> None: ...

    def register_before_execute_hook(self, hook: Callable[[str], None]) -> None: ...
    def unregister_before_execute_hook(self, hook: Callable[[str], None]) -> None: ...
    def trigger_before_execute_hooks(self, code: str) -> None: ...

    def register_after_execute_hook(self, hook: Callable[[object], None]) -> None: ...
    def unregister_after_execute_hook(self, hook: Callable[[object], None]) -> None: ...
    def trigger_after_execute_hooks(self, result: object) -> None: ...

    def register_on_error_hook(self, hook: Callable[[Exception], None]) -> None: ...
    def unregister_on_error_hook(self, hook: Callable[[Exception], None]) -> None: ...
    def trigger_on_error_hooks(self, error: Exception) -> None: ...
```

Global instance: `kernel_hooks = KernelHooks()`

---

### MIME Type Functions

```python
# Registration
def register_handler(module, cls, mimetype, serializer, deserializer): ...
def register_pandas_handlers(): ...
def register_ndarray_handlers(): ...
def register_image_handlers(): ...
def register_array_handlers(): ...

# Serialization
def serialize_object(obj) -> tuple[dict, dict]: ...
def deserialize_object(data, metadata=None) -> Any: ...

# High-level API
def serialize_value(value) -> dict: ...      # → {"data": {...}, "metadata": {...}}
def deserialize_value(data, metadata) -> Any: ...
```

---

## `agent_jupyter_toolkit.notebook`

### `NotebookDocumentTransport` (Protocol)

Runtime-checkable protocol for notebook document operations.

| Method | Signature | Description |
|--------|-----------|-------------|
| `start()` | `async def start() -> None` | Initialize (idempotent) |
| `stop()` | `async def stop() -> None` | Release resources (fault-tolerant) |
| `is_connected()` | `async def is_connected() -> bool` | Ready for operations |
| `fetch()` | `async def fetch() -> dict` | Get notebook as nbformat dict |
| `save(content)` | `async def save(dict) -> None` | Persist notebook |
| `append_code_cell(source, ...)` | `async def ... -> int` | Append code cell |
| `insert_code_cell(index, source, ...)` | `async def ... -> None` | Insert code cell |
| `append_markdown_cell(source, ...)` | `async def ... -> int` | Append markdown cell |
| `insert_markdown_cell(index, source, ...)` | `async def ... -> None` | Insert markdown cell |
| `update_cell_outputs(index, outputs, count)` | `async def ... -> None` | Replace cell outputs |
| `set_cell_source(index, source)` | `async def ... -> None` | Replace cell source |
| `delete_cell(index)` | `async def ... -> None` | Delete cell |
| `on_change(callback)` | `def ... -> None` | Register change observer |

Implementations: `LocalFileDocumentTransport`, `ContentsApiDocumentTransport`,
`CollabYjsDocumentTransport`.

---

### `NotebookSession`

```python
@dataclass
class NotebookSession:
    kernel: KernelSession
    doc: NotebookDocumentTransport
```

| Method | Signature | Returns |
|--------|-----------|---------|
| `start()` | `async def start() -> None` | Start kernel then doc |
| `stop()` | `async def stop() -> None` | Stop doc then kernel |
| `append_and_run(code, *, metadata=None, timeout=None)` | `async def ... -> tuple[int, ExecutionResult]` | Append + execute cell |
| `run_at(index, code, *, timeout=None)` | `async def ... -> ExecutionResult` | Update + execute cell. Raises `TypeError` if the cell is not a code cell. |
| `run_markdown(text, *, index=None)` | `async def ... -> int` | Insert markdown cell |
| `is_connected()` | `async def ... -> bool` | Both live |

Supports `async with`.

---

### `NotebookBuffer`

In-memory `MutableSequence` wrapper over a transport.

| Method | Signature | Description |
|--------|-----------|-------------|
| `load()` | `async def load() -> None` | Fetch into memory |
| `commit()` | `async def commit() -> None` | Persist if dirty |
| `append_code_cell(source, ...)` | `def ... -> int` | Sync append |
| `append_markdown_cell(source, ...)` | `def ... -> int` | Sync append |
| `set_cell_source(index, source)` | `def ... -> None` | Sync update |
| `update_cell_outputs(index, outputs, count)` | `def ... -> None` | Sync update |

| Property | Type | Description |
|----------|------|-------------|
| `dirty` | `bool` | Uncommitted changes |
| `metadata` | `dict` | Notebook metadata (read/write) |
| `nbformat` | `int` | Major version |
| `nbformat_minor` | `int` | Minor version |

---

### `make_document_transport()`

```python
def make_document_transport(
    mode: str,
    *,
    local_path: str | None,
    remote_base: str | None,
    remote_path: str | None,
    token: str | None,
    headers_json: str | None,
    prefer_collab: bool = False,
    create_if_missing: bool = False,
    local_autosave_delay: float | None = None,
) -> NotebookDocumentTransport
```

### Cell Helpers

```python
def create_code_cell(source, metadata=None, outputs=None, execution_count=None) -> NotebookNode
def create_markdown_cell(source, metadata=None) -> NotebookNode
```

### Notebook Types

```python
@dataclass
class NotebookCodeExecutionResult:
    status: str = "ok"
    execution_count: int | None = None
    cell_index: int = -1
    stdout: str = ""
    stderr: str = ""
    outputs: list[dict] = field(...)
    text_outputs: list[str] = field(...)
    formatted_output: str = ""
    error_message: str | None = None
    elapsed_seconds: float | None = None

@dataclass
class NotebookMarkdownCellResult:
    status: str = "ok"
    cell_index: int | None = None
    error_message: str | None = None
    elapsed_seconds: float | None = None
```

---

## `agent_jupyter_toolkit.utils`

### Factory Functions

```python
def create_kernel(mode="local", *, kernel_name="python3", ...) -> Session
def create_notebook_transport(mode, path, *, base_url=None, ...) -> NotebookDocumentTransport
```

### Execution Functions

```python
async def execute_code(session, code, *, timeout=120.0, format_outputs=True)
    -> NotebookCodeExecutionResult

async def invoke_code_cell(notebook_session, code, *, timeout=120.0, format_outputs=True)
    -> NotebookCodeExecutionResult

async def invoke_existing_cell(notebook_session, *, index, code, timeout=120.0, format_outputs=True)
    -> NotebookCodeExecutionResult

async def invoke_markdown_cell(notebook_session, text, *, index=None)
    -> NotebookMarkdownCellResult

async def invoke_notebook_cells(notebook_session, cells, *, timeout=120.0)
    -> list[NotebookCodeExecutionResult]

async def get_session_info(notebook_session) -> dict

async def get_variables(session) -> list[str]

async def get_variable_value(session, name) -> Any

def convert_to_notebook_result(result, cell_index, *, elapsed_seconds=None, format_outputs=True)
    -> NotebookCodeExecutionResult
```

### Output Functions

```python
def extract_outputs(outputs: list[dict]) -> list[str]
def format_output(outputs: list[dict], *, include_types=False, max_length=1000) -> str
def get_result_value(outputs: list[dict]) -> Any | None
def has_error(outputs: list[dict]) -> bool
```

### Package Management

```python
async def check_package_availability(session, packages, timeout=120.0) -> dict[str, bool]
async def ensure_packages(session, packages, timeout=120.0) -> bool
async def ensure_packages_with_report(session, packages, timeout=120.0) -> dict
async def install_package(session, package, timeout=60.0) -> bool
async def update_dependencies(session, packages, timeout=120.0) -> bool

# Pre-defined package groups
SCIENTIFIC_PACKAGES: list[str]
ML_PACKAGES: list[str]
DATA_VIZ_PACKAGES: list[str]
WEB_PACKAGES: list[str]
```

### Notebook File Helpers

```python
def create_minimal_notebook_content() -> dict
async def create_notebook_via_contents_api(base_url, path, token=None, headers=None) -> None
```
