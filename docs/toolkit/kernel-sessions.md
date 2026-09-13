# Kernel Sessions

The kernel subsystem provides async-first code execution against Jupyter
kernels. It supports local (ZMQ) and remote (HTTP+WebSocket) transports
through a single `Session` interface.

## Creating a Session

### Local kernel

```python
from agent_jupyter_toolkit.kernel import create_session, SessionConfig

session = create_session(SessionConfig(
    mode="local",
    kernel_name="python3",  # any installed kernelspec
))
```

### Remote kernel (Jupyter Server)

```python
from agent_jupyter_toolkit.kernel import create_session, SessionConfig, ServerConfig

session = create_session(SessionConfig(
    mode="server",
    server=ServerConfig(
        base_url="http://localhost:8888",
        token="YOUR_TOKEN",
        kernel_name="python3",
        notebook_path="analysis.ipynb",  # optional: bind to a notebook
        headers={"Cookie": "..."},       # optional: extra headers
    ),
))
```

### Async context manager

Sessions implement `__aenter__` / `__aexit__` for automatic lifecycle
management:

```python
async with create_session(config) as session:
    result = await session.execute("print('hello')")
# a kernel created by this session is shut down here; a borrowed kernel is preserved
```

Or manage manually:

```python
session = create_session(config)
await session.start()
try:
    result = await session.execute("print('hello')")
finally:
    await session.shutdown()
```

## Code Execution

### Basic execution

```python
result = await session.execute("x = 42\nprint(x)")

print(result.status)           # "ok" or "error"
print(result.execution_count)  # 1
print(result.stdout)           # "42\n"
print(result.stderr)           # ""
print(result.outputs)          # list of nbformat output dicts
```

### With timeout

```python
result = await session.execute("import time; time.sleep(300)", timeout=5.0)
if result.timed_out:
    print(result.outcome)  # "unknown": completion was not observed
```

### Real-time output streaming

Provide an `output_callback` to receive outputs as they arrive from the
kernel. This is how `NotebookSession` mirrors outputs into a document in
real time:

```python
async def on_output(outputs: list[dict], exec_count: int | None):
    print(f"[{exec_count}] {len(outputs)} outputs so far")

result = await session.execute(
    "for i in range(5):\n    print(i)",
    output_callback=on_output,
)
```

Callbacks arrive during execution in message order. `outputs` is a cumulative
nbformat-like snapshot of the current cell state. Both transports collect
kernel messages independently from callback delivery and retain the newest
pending snapshot, so a slow callback cannot stall IOPub intake. Intermediate
snapshots may be coalesced and `callback_snapshots_coalesced` reports how many.

Each callback call has a 30-second default limit, configurable through
`SessionConfig.output_callback_timeout` or
`ServerConfig.output_callback_timeout`; use `None` to disable it. Callback
failure, cancellation, and timeout leave the kernel `status` unchanged and are
reported through `callback_status` and `callback_error`. Cancelling the owning
execution task still cancels callback delivery. A final snapshot is attempted
for completed and timed-out executions, and disconnect errors retain partial
results.

### Execution options

```python
result = await session.execute(
    code,
    timeout=30.0,           # max seconds (None = no limit)
    output_callback=cb,     # real-time streaming
    silent=False,           # suppress ordinary output when supported
    store_history=True,     # record in kernel history
    user_expressions={"total": "sum(values)"},
    metadata={"cellId": "stable-cell-id"},
    subshell_id=None,        # optional ID from create_subshell()
    allow_stdin=False,      # enable kernel stdin requests
    stop_on_error=True,     # abort queue on error
)
```

Results also report `request_id`, `cell_id`, `source_hash`,
`kernel_generation`, `persistence_status`, `output_truncated`,
`dropped_output_bytes`, `callback_status`, `callback_error`,
`callback_snapshots_coalesced`, `outcome`, and `timed_out`. The default output
budget is 50 MiB for retained output and display-update sidecars. Stream chunks
are accumulated without repeatedly copying all preceding text. A timeout or
disconnect does not prove the kernel skipped the code,
so check kernel state before retrying work with side effects.

## Kernel Introspection

### Tab completion

```python
result = await session.complete("import ma", cursor_pos=9)

print(result.matches)       # ["math", "marshal", ...]
print(result.cursor_start)  # 7
print(result.cursor_end)    # 9
print(result.status)        # "ok"
```

### Object inspection

```python
result = await session.inspect("len", cursor_pos=3, detail_level=1)

print(result.found)     # True
print(result.data)      # {"text/plain": "Signature: len(obj, /)\n..."}
print(result.status)    # "ok"
```

`detail_level=0` returns a summary; `detail_level=1` returns full
documentation.

### Code-completeness check

Useful for multi-line input editors to decide whether to execute or add a
newline:

```python
result = await session.is_complete("def foo():")

print(result.status)  # "incomplete"
print(result.indent)  # "    "
```

Possible status values: `"complete"`, `"incomplete"`, `"invalid"`, `"unknown"`.

### Kernel info

```python
info = await session.kernel_info()

print(info.protocol_version)       # "5.4"
print(info.implementation)         # "ipython"
print(info.implementation_version) # "8.x.x"
print(info.language_info)          # {"name": "python", "version": "3.11.x", ...}
print(info.banner)                 # IPython startup banner
print(info.supported_features)     # explicit capabilities advertised by the kernel
print(info.help_links)
```

## Kernel Control

### Interrupt

Cancel a long-running or hung execution:

```python
import asyncio

# Start a long computation
task = asyncio.create_task(session.execute("import time; time.sleep(3600)"))

# Interrupt after 2 seconds
await asyncio.sleep(2)
await session.interrupt()

result = await task
print(result.status)  # "error" (KeyboardInterrupt)
```

> **Note:** Interrupt requires a managed kernel (local mode). For remote
> sessions, it sends `POST /api/kernels/{id}/interrupt` to the server.

### Subshells and debugging

Kernels advertise optional workflows through `kernel_info().supported_features`.
The toolkit checks those capabilities before sending control-channel requests
and raises `UnsupportedKernelCapabilityError` when a feature is unavailable.

```python
info = await session.kernel_info()

if "kernel subshells" in info.supported_features:
    subshell_id = await session.create_subshell()
    try:
        result = await session.execute("worker_state = 1", subshell_id=subshell_id)
        print(await session.list_subshells())
    finally:
        await session.delete_subshell(subshell_id)

if "debugger" in info.supported_features:
    response = await session.debug({
        "seq": 1,
        "type": "request",
        "command": "debugInfo",
    })
```

Subshells require a kernel implementing the Jupyter subshell protocol. Debug
payloads and responses use the Debug Adapter Protocol shape expected by the
kernel. Shell and control replies are routed by parent request ID, allowing a
debug control request to proceed while an execution is paused. Query
`kernel_info()` before starting that execution so the capability is cached.

### Execution history

Retrieve previous inputs/outputs from the kernel's history database:

```python
result = await session.history(n=5, output=True)

for entry in result.history:
    print(f"[{entry.session}:{entry.line_number}] {entry.input}")
    if entry.output:
        print(f"  → {entry.output}")
```

Parameters:
- `hist_access_type`: `"tail"` (last N), `"range"`, or `"search"`
- `n`: number of entries for `"tail"` / max results for `"search"`
- `output`: include output text alongside input
- `raw`: return raw (un-transformed) input
- `session`, `start`, `stop`: history session and bounds for range requests
- `pattern`, `unique`: match expression and deduplication for search requests

### Ownership and session identity

```python
identity = session.session_info()
print(identity.kernel_id, identity.server_session_id)
print(identity.owns_kernel, identity.owns_session)
print(identity.kernel_generation, identity.encryption_enabled)
```

`shutdown()` terminates resources created by this client and detaches from
borrowed kernels. Use `shutdown_kernel()` for an explicit destructive shutdown
of an attached kernel. Remote notebook sessions reuse the unique Sessions API
kernel for `ServerConfig.notebook_path`; ambiguous duplicates raise an error.

## Variable Management

`VariableManager` provides language-agnostic variable get/set/list operations:

```python
from agent_jupyter_toolkit.kernel.variables import VariableManager

vm = VariableManager(session, language="python")

# Set a variable (serialized via base64-encoded JSON for safety)
await vm.set("data", [1, 2, 3])

# Get a variable
value = await vm.get("data")
print(value)  # [1, 2, 3]

# List all user variables
names = await vm.list()
print(names)  # ["data"]

# List with type/size metadata
detailed = await vm.list(detailed=True)
for v in detailed:
    print(f"{v.name}: {v.type} ({v.size} bytes)")
```

Variable names are validated as legal Python identifiers. Values are
transferred using base64-encoded JSON to prevent string injection. `set()`
accepts finite, JSON-compatible values only, and `get()` raises if a kernel
value cannot be represented as strict JSON. Construct DataFrames, arrays, and
custom Python objects with normal kernel code or the MIME serialization APIs.

## Execution Hooks

Register callbacks for kernel lifecycle events:

```python
from agent_jupyter_toolkit.kernel.hooks import kernel_hooks

# Before execution
def on_before(code: str):
    print(f"About to execute: {code[:50]}")
kernel_hooks.register_before_execute_hook(on_before)

# After execution
def on_after(result):
    print(f"Execution finished: {result.status}")
kernel_hooks.register_after_execute_hook(on_after)

# Output messages
def on_output(msg: dict):
    print(f"IOPub: {msg.get('msg_type')}")
kernel_hooks.register_output_hook(on_output)

# Error handling
def on_error(exc: Exception):
    print(f"Error: {exc}")
kernel_hooks.register_on_error_hook(on_error)
```

Hooks are global (singleton), thread-safe, and swallow exceptions to avoid
breaking kernel execution.

## MIME Type Serialization

The `mimetypes` module provides extensible object serialization via a MIME
type registry:

```python
from agent_jupyter_toolkit.kernel.serialization import serialize_value, deserialize_value

# Serialize any object
bundle = serialize_value({"key": "value"})
# → {"data": {"application/json": '...'}, "metadata": {...}}

# Deserialize back
obj = deserialize_value(bundle["data"], bundle["metadata"])
```

### Built-in handlers

| Type | MIME type | Notes |
|------|-----------|-------|
| `pandas.DataFrame` | `application/vnd.apache.arrow.stream` | Primary: Arrow IPC |
| `pandas.DataFrame` | `application/json` | Fallback: JSON |
| `numpy.ndarray` | `application/json` | As nested list |
| `PIL.Image` | `image/png` | Base64-encoded PNG |
| `array.array` | `application/json` | As list |
| Any JSON-serializable | `application/json` | Default |
| Everything else | `application/python-pickle` | Last resort (trusted data only) |

### Custom handlers

```python
from agent_jupyter_toolkit.kernel.mimetypes import register_handler

def serialize_my_type(obj):
    return obj.to_dict()

def deserialize_my_type(data, mimetype):
    return MyType.from_dict(data)

register_handler("mypackage.types", "MyType", "application/json",
                  serialize_my_type, deserialize_my_type)
```

## Advanced: KernelManager

For local sessions, access the underlying `KernelManager` for low-level
operations:

```python
km = session.kernel_manager  # None for remote sessions

if km:
    print(km.connection_file_path)   # path to connection JSON
    print(await km.is_healthy())     # kernel_info round-trip check
    await km.restart()               # restart the kernel process
```

## Result Types

| Type | Fields | Description |
|------|--------|-------------|
| `ExecutionResult` | `status`, `execution_count`, `stdout`, `stderr`, `outputs` | Basic execution result |
| `CompleteResult` | `matches`, `cursor_start`, `cursor_end`, `status`, `metadata` | Tab-completion |
| `InspectResult` | `found`, `data`, `metadata`, `status` | Object inspection |
| `IsCompleteResult` | `status`, `indent` | Code-completeness check |
| `HistoryResult` | `history` (list of `HistoryEntry`), `status` | Kernel history |
| `HistoryEntry` | `session`, `line_number`, `input`, `output` | Single history entry |
| `KernelInfoResult` | `protocol_version`, `implementation`, `language_info`, `banner`, `status` | Kernel metadata |

## Error Handling

```python
from agent_jupyter_toolkit.kernel import KernelError, KernelExecutionError, KernelTimeoutError

try:
    result = await session.execute("1/0")
except KernelExecutionError as e:
    print(f"Execution failed: {e}")
except KernelTimeoutError as e:
    print(f"Timed out: {e}")
except KernelError as e:
    print(f"Kernel error: {e}")
```

Note that most execution errors are reported via `result.status == "error"`
rather than exceptions. Exceptions are reserved for transport-level failures
(kernel died, network timeout, etc.).
