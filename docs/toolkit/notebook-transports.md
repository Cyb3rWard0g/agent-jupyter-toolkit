# Notebook Transports

The notebook subsystem provides a transport abstraction over Jupyter notebook
documents. It supports three storage backends through the
`NotebookDocumentTransport` protocol, plus higher-level wrappers for common
workflows.

## Transport Protocol

All notebook transports implement the `NotebookDocumentTransport` runtime-checkable
protocol. This provides a uniform interface regardless of backing storage:

```python
from agent_jupyter_toolkit.notebook import NotebookDocumentTransport

# All transports implement:
await transport.start()
await transport.stop()
await transport.is_connected()
await transport.fetch()              # → nbformat dict
await transport.save(content)
await transport.append_code_cell(source, metadata=None, tags=None)
await transport.insert_code_cell(index, source, metadata=None, tags=None)
await transport.append_markdown_cell(source, tags=None)
await transport.insert_markdown_cell(index, source, tags=None)
await transport.update_cell_outputs(index, outputs, execution_count)
await transport.set_cell_source(index, source)
await transport.delete_cell(index)
transport.on_change(callback)
```

All cell indices are **zero-based**. Out-of-range indices raise `IndexError`.

## Transport Implementations

### 1. Local File Transport

Reads and writes `.ipynb` files directly on the filesystem.

```python
from agent_jupyter_toolkit.notebook import make_document_transport

transport = make_document_transport(
    "local",
    local_path="notebooks/analysis.ipynb",
    remote_base=None,
    remote_path=None,
    token=None,
    headers_json=None,
    create_if_missing=True,
)
```

Features:
- Thread-safe read/write via asyncio lock
- Atomic saves (write to temp file, then rename)
- Optional **debounced autosave** — batches rapid edits into a single write
- Path security via configurable allowlist (see [Configuration](configuration.md))

#### Autosave

```python
transport = make_document_transport(
    "local",
    local_path="analysis.ipynb",
    remote_base=None,
    remote_path=None,
    token=None,
    headers_json=None,
    local_autosave_delay=2.0,  # seconds
)
```

With autosave enabled, `save()` is debounced — multiple rapid writes within
the delay window are coalesced into a single disk write. Call
`await transport.flush()` to force an immediate write.

### 2. Contents API Transport

Communicates with a Jupyter Server via the [Contents REST API](https://jupyter-server.readthedocs.io/en/latest/developers/rest-api.html).

```python
transport = make_document_transport(
    "server",
    local_path=None,
    remote_base="http://localhost:8888",
    remote_path="notebooks/analysis.ipynb",
    token="YOUR_TOKEN",
    headers_json=None,
    create_if_missing=True,
)
```

Features:
- `GET /api/contents/{path}` for fetch
- `PUT /api/contents/{path}` for save
- Token and cookie-based authentication
- Automatic notebook creation via PUT when `create_if_missing=True`

### 3. Collaborative Yjs/CRDT Transport

Real-time collaborative editing using the Yjs/CRDT protocol over WebSocket.
Requires `jupyter-collaboration` on the server.

```python
transport = make_document_transport(
    "server",
    local_path=None,
    remote_base="http://localhost:8888",
    remote_path="notebooks/shared.ipynb",
    token="YOUR_TOKEN",
    headers_json=None,
    prefer_collab=True,
    create_if_missing=True,
)
```

Features:
- Real-time document sync via Yjs/CRDT (pycrdt)
- Conflict-free concurrent editing from multiple clients
- Cell mutations applied as CRDT operations (merge, don't overwrite)
- **Awareness protocol** — broadcast presence metadata (cursors, user info)
- Automatic reconnection and state recovery
- **Default empty cell stripping** — JupyterLab adds a blank code cell to every new notebook; the transport removes it on `start()` so the notebook begins truly empty
- Falls back to Contents API transport if `prefer_collab=False`

#### Awareness (presence metadata)

```python
# Set your identity/state (visible to other connected clients)
await transport.set_awareness_state({
    "user": {"name": "agent-1", "role": "assistant"},
    "cursor": {"cell": 3, "line": 10},
})

# Read all connected clients' awareness states
states = await transport.get_awareness_states()
for client_id, state in states.items():
    print(f"Client {client_id}: {state}")
```

Awareness metadata is ephemeral — it is not persisted in the `.ipynb` file.
It disappears when a client disconnects.

#### Change observers

```python
def on_doc_change(event: dict):
    print(f"Document changed: {event}")

transport.on_change(on_doc_change)
```

Events include `{"op": "y-update"}` for CRDT state changes, and
`{"op": "cells-mutated", "index": N}` for individual cell mutations.

## Factory Function

`make_document_transport()` selects the appropriate implementation:

```python
from agent_jupyter_toolkit.notebook import make_document_transport

transport = make_document_transport(
    mode,            # "local" or "server"
    local_path=...,
    remote_base=...,
    remote_path=...,
    token=...,
    headers_json=...,
    prefer_collab=False,
    create_if_missing=False,
    local_autosave_delay=None,
)
```

| `mode` | `prefer_collab` | Result |
|--------|----------------|--------|
| `"local"` | — | `LocalFileDocumentTransport` |
| `"server"` | `False` | `ContentsApiDocumentTransport` |
| `"server"` | `True` | `CollabYjsDocumentTransport` |
| invalid config | — | No-op fallback transport |

## NotebookSession

`NotebookSession` combines a kernel session with a document transport for a
complete "execute code → persist results" workflow:

```python
from agent_jupyter_toolkit.kernel import create_session, SessionConfig
from agent_jupyter_toolkit.notebook import NotebookSession, make_document_transport

kernel = create_session(SessionConfig(mode="local"))
doc = make_document_transport("local", local_path="analysis.ipynb",
                              remote_base=None, remote_path=None,
                              token=None, headers_json=None,
                              create_if_missing=True)

async with NotebookSession(kernel=kernel, doc=doc) as nb:
    # Append and execute a code cell
    idx, result = await nb.append_and_run("print('hello')")

    # Update an existing cell
    result = await nb.run_at(0, "print('updated')")

    # Add a markdown cell
    idx = await nb.run_markdown("# Results")

    # Check connectivity
    connected = await nb.is_connected()
```

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `start()` | `None` | Start kernel then document (idempotent) |
| `stop()` | `None` | Stop document then kernel (fault-tolerant) |
| `append_and_run(code, timeout=None)` | `(int, ExecutionResult)` | Append cell, execute, stream outputs |
| `run_at(index, code, timeout=None)` | `ExecutionResult` | Replace cell source and execute. Raises `TypeError` if the target is not a code cell. |
| `run_markdown(text, index=None)` | `int` | Insert/append a markdown cell |
| `is_connected()` | `bool` | Both kernel and document are live |

### Streaming behavior

During execution, outputs are streamed to the document cell in real time:

1. Each IOPub message updates the cell immediately (delta or full replace)
2. Execution count changes propagate to the cell
3. `clear_output` messages clear accumulated outputs
4. A final authoritative write with normalized nbformat outputs happens at the
   end of execution

## NotebookBuffer

`NotebookBuffer` is an in-memory, `MutableSequence`-based wrapper for staged
edits with explicit commit:

```python
from agent_jupyter_toolkit.notebook import NotebookBuffer, make_document_transport

transport = make_document_transport("local", local_path="notebook.ipynb",
                                    remote_base=None, remote_path=None,
                                    token=None, headers_json=None)

buf = NotebookBuffer(transport)
await buf.load()

# Stage edits in memory (no disk I/O)
buf.append_code_cell("x = 1")
buf.append_markdown_cell("# Header")
buf.set_cell_source(0, "x = 42")

print(buf.dirty)  # True
print(len(buf))   # 2

# Single write to persist everything
await buf.commit()
print(buf.dirty)  # False
```

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `load()` | `None` | Fetch notebook from transport into memory |
| `commit()` | `None` | Persist in-memory notebook if dirty |
| `append_code_cell(source, ...)` | `int` | Add code cell, return index |
| `append_markdown_cell(source, ...)` | `int` | Add markdown cell, return index |
| `set_cell_source(index, source)` | `None` | Replace cell source text |
| `update_cell_outputs(index, outputs, count)` | `None` | Replace cell outputs |

`NotebookBuffer` also supports standard `MutableSequence` operations:
`__getitem__`, `__setitem__`, `__delitem__`, `__len__`, `insert()`.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `dirty` | `bool` | Has uncommitted changes |
| `metadata` | `dict` | Notebook-level metadata (read/write) |
| `nbformat` | `int` | Major format version |
| `nbformat_minor` | `int` | Minor format version |

## Cell Creation Helpers

Low-level helpers for creating nbformat-compliant cells:

```python
from agent_jupyter_toolkit.notebook import create_code_cell, create_markdown_cell

code_cell = create_code_cell(
    source="import pandas as pd",
    metadata={"tags": ["setup"]},
    outputs=[],
    execution_count=None,
)

md_cell = create_markdown_cell(
    source="# Introduction\n\nThis notebook analyzes...",
    metadata={"tags": ["documentation"]},
)
```

Both return `nbformat.NotebookNode` instances.

## Result Types

| Type | Fields | Description |
|------|--------|-------------|
| `NotebookCodeExecutionResult` | `status`, `cell_index`, `stdout`, `stderr`, `outputs`, `text_outputs`, `formatted_output`, `error_message`, `elapsed_seconds` | Enhanced execution result with notebook context |
| `NotebookMarkdownCellResult` | `status`, `cell_index`, `error_message`, `elapsed_seconds` | Result of markdown cell insertion |
