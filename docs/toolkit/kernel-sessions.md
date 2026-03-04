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
# kernel is shut down here
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
import asyncio

try:
    result = await session.execute("import time; time.sleep(300)", timeout=5.0)
except asyncio.TimeoutError:
    print("Execution timed out")
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

The callback is awaited in strict message order. `outputs` is a cumulative
list of nbformat-like dicts representing the current cell state.

### Execution options

```python
result = await session.execute(
    code,
    timeout=30.0,           # max seconds (None = no limit)
    output_callback=cb,     # real-time streaming
    store_history=True,     # record in kernel history
    allow_stdin=False,      # enable kernel stdin requests
    stop_on_error=True,     # abort queue on error
)
```

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
transferred using base64-encoded JSON to prevent string injection.

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
