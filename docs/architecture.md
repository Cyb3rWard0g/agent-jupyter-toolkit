# Architecture

This document describes the internal architecture of the `agent-jupyter-toolkit`
monorepo, the design patterns used, and how the components interact.

## Repository Layout

This is a **uv workspace** monorepo containing two publishable packages:

```
agent-jupyter-toolkit/              # repo root (workspace)
├── pyproject.toml                  # Workspace root (private, not published)
├── uv.lock                         # Single lockfile for the workspace
├── tox.ini                         # Shared test/lint orchestration
├── packages/
│   ├── agent-jupyter-toolkit/      # Domain library (published to PyPI)
│   │   ├── pyproject.toml
│   │   ├── src/agent_jupyter_toolkit/
│   │   └── tests/
│   └── mcp-jupyter-notebook/       # MCP server (published to PyPI)
│       ├── pyproject.toml
│       ├── src/mcp_jupyter_notebook/
│       └── tests/
├── docs/
├── CONTRIBUTING.md
└── repos/                          # Reference repos (git-ignored)
```

The MCP server depends on the toolkit via `workspace = true` linking
(resolved locally during development, from PyPI when published).

## Toolkit Package Layout

```
packages/agent-jupyter-toolkit/src/agent_jupyter_toolkit/
├── __init__.py                     # Lazy-loaded top-level package
├── py.typed                        # PEP 561 type marker
│
├── kernel/                         # Kernel execution subsystem
│   ├── __init__.py                 # Public exports (Session, types, create_session)
│   ├── types.py                    # Dataclasses: SessionConfig, ExecutionResult, etc.
│   ├── transport.py                # KernelTransport base protocol
│   ├── session.py                  # Session wrapper + create_session() factory
│   ├── manager.py                  # KernelManager (AsyncKernelManager lifecycle)
│   ├── messages.py                 # Wire-protocol message builders (server transport)
│   ├── hooks.py                    # KernelHooks callback registry
│   ├── variables.py                # VariableManager (get/set/list kernel vars)
│   ├── variable_ops.py             # Language-specific code templates
│   ├── mimetypes.py                # MIME handler registry for serialization
│   ├── serialization.py            # High-level serialize/deserialize API
│   └── transports/
│       ├── local.py                # LocalTransport (ZMQ via jupyter_client)
│       └── server.py               # ServerTransport (HTTP+WS to Jupyter Server)
│
├── notebook/                       # Notebook document subsystem
│   ├── __init__.py                 # Public exports
│   ├── types.py                    # Dataclasses: NotebookCodeExecutionResult, etc.
│   ├── transport.py                # NotebookDocumentTransport protocol
│   ├── session.py                  # NotebookSession (kernel + document orchestration)
│   ├── workspace.py                # NotebookWorkspace registry, defaults, and lifecycle
│   ├── factory.py                  # make_document_transport() factory
│   ├── buffer.py                   # NotebookBuffer (in-memory staged edits)
│   ├── cells.py                    # create_code_cell(), create_markdown_cell()
│   ├── config.py                   # Environment-based Config dataclass
│   ├── utils.py                    # Path validation, output normalization, file I/O
│   └── transports/
│       ├── __init__.py
│       ├── local_file.py           # LocalFileDocumentTransport (filesystem)
│       ├── contents.py             # ContentsApiDocumentTransport (REST)
│       └── collab/
│           ├── __init__.py
│           ├── transport.py        # CollabYjsDocumentTransport (Yjs/CRDT)
│           ├── protocol.py         # y-websocket sync protocol helpers
│           └── yutils.py           # CRDT cell construction utilities
│
└── utils/                          # High-level convenience functions
    ├── __init__.py                 # Public exports
    ├── execution.py                # execute_code, invoke_code_cell, etc.
    ├── factories.py                # create_kernel, create_notebook_transport
    ├── outputs.py                  # extract_outputs, format_output, get_result_value
    └── packages.py                 # Package management (ensure_packages, install, etc.)
```

## Design Patterns

### Transport Pattern

The core architectural pattern is the **Transport abstraction**. Both the
kernel and notebook subsystems define a protocol (abstract interface) that
concrete implementations fulfill:

```
┌─────────────┐       ┌──────────────────┐
│   Session    │──────▶│  KernelTransport │ (protocol)
└─────────────┘       └──────────────────┘
                             ▲
                    ┌────────┴────────┐
                    │                 │
             ┌──────┴─────┐   ┌──────┴──────┐
             │   Local    │   │   Server    │
             │ Transport  │   │  Transport  │
             │   (ZMQ)    │   │ (HTTP+WS)   │
             └────────────┘   └─────────────┘
```

```
┌──────────────────┐       ┌────────────────────────────┐
│  NotebookSession │──────▶│ NotebookDocumentTransport  │ (protocol)
└──────────────────┘       └────────────────────────────┘
                                       ▲
                          ┌────────────┼────────────┐
                          │            │            │
                   ┌──────┴─────┐ ┌────┴────┐ ┌────┴───────┐
                   │ Local File │ │Contents │ │   Collab   │
                   │ Transport  │ │   API   │ │ Yjs/CRDT   │
                   └────────────┘ └─────────┘ └────────────┘
```

This allows the higher-level `Session` and `NotebookSession` classes to work
identically regardless of whether the kernel/notebook is local or remote.

### Factory Pattern

Factory functions decouple transport selection from consumer code:

- `create_session(SessionConfig)` → `Session`
- `make_document_transport(mode, ...)` → `NotebookDocumentTransport`
- `create_kernel(mode, ...)` → `Session` (utility shorthand)
- `create_notebook_transport(mode, ...)` → `NotebookDocumentTransport` (utility shorthand)

Configuration flows through dataclasses (`SessionConfig`, `ServerConfig`) to
ensure type safety and IDE autocompletion.

### Callback / Hook Pattern

The toolkit uses callbacks at multiple levels:

1. **Output streaming** — `output_callback` in `execute()` receives ordered,
   cumulative snapshots from a bounded dispatcher. Slow callbacks are
   coalesced and callback failures are reported separately from execution.
2. **Execution hooks** — `KernelHooks` singleton for pre/post execution
   instrumentation
3. **Change observers** — `on_change()` on document transports for reactive
   UI updates
4. **Awareness callbacks** — CRDT awareness state changes for collaborative
   presence

### Protocol (structural typing)

`NotebookDocumentTransport` is a `@runtime_checkable Protocol`, allowing duck
typing. Any class implementing the required methods works:

```python
from agent_jupyter_toolkit.notebook import NotebookDocumentTransport

class MyCustomTransport:
    async def start(self): ...
    async def stop(self): ...
    # ... implement all protocol methods

assert isinstance(MyCustomTransport(), NotebookDocumentTransport)  # True
```

## Notebook Workspace

`agent_jupyter_toolkit.notebook.NotebookWorkspace` manages multiple
`NotebookSession` objects. `NotebookWorkspaceConfig` holds Jupyter backend
settings independently of any agent framework. The workspace owns path lookup,
default selection, session creation, file discovery/deletion, and the concurrent
open/close/delete/shutdown barriers.

The MCP package's `context.SessionManager` is a compatibility adapter: it converts
MCP configuration into `NotebookWorkspaceConfig` and inherits the core behavior.
`AppContext` makes that workspace available to tools. Existing tool arguments
remain unchanged; calls omitting `notebook_path` resolve the workspace default.

This is an in-process component. A remote MCP deployment runs the workspace in
its server environment and connects to Jupyter's APIs; clients do not install a
separate workspace service. Python applications can use the workspace directly.
There is one default per workspace, shared by its callers. Extraction into the
core does not introduce per-agent isolation or persistent run scheduling.

See the [workspace API](toolkit/api-reference.md#notebookworkspace-and-notebookworkspaceconfig)
and [MCP notebook lifecycle](mcp-server/tools.md#notebook-lifecycle) for examples.

## Data Flow

### Code Execution (Local)

```
User code
    │
    ▼
Session.execute(code)
    │
    ▼
LocalTransport.execute(code)
    │
    ├─▶ KernelManager.client.execute_interactive(code)
    │       │
    │       ▼
    │   IPython kernel (ZMQ)
    │       │
    │       ▼
    │   IOPub messages (stream, display_data, execute_result, error)
    │       │
    │       ▼
    │   output_hook() — updates state and signals callback dispatcher
    │
    ▼
ExecutionResult (status, outputs, stdout, stderr)
```

### Code Execution (Server)

```
User code
    │
    ▼
Session.execute(code)
    │
    ▼
ServerTransport.execute(code)
    │
    ├─▶ POST /api/kernels/{id}/channels (WebSocket)
    │       │
    │       ▼
    │   shell_reply + IOPub messages over WebSocket
    │       │
    │       ▼
    │   fold_iopub_events() — accumulates outputs
    │
    ▼
ExecutionResult
```

### Notebook Session Execution

```
NotebookSession.append_and_run(code)
    │
    ├─▶ doc.append_code_cell_with_id(code) → capture cell ID atomically
    │
    ├─▶ kernel.execute(code, output_callback=streaming_cb)
    │       │
    │       ▼
    │   Incremental reducer snapshots:
    │       streaming_cb → doc.update_cell_outputs_by_id(id, outputs, count)
    │
    ├─▶ Final: validate ID/source and persist nbformat outputs
    │
    ▼
(cell_index, ExecutionResult)
```

## Dependencies

The toolkit's dependencies are layered to minimize the install footprint for
simple use cases:

### Core (always required)

| Package | Purpose |
|---------|---------|
| `jupyter_client` | ZMQ kernel management, wire protocol |
| `nbformat` | Notebook format read/write/validation |
| `aiohttp` | HTTP client for server transports |

### Collaboration client

| Package | Purpose |
|---------|---------|
| `pycrdt` | CRDT types (Doc, Array, Map, Text, Awareness) |
| `jupyter_ydoc` | YNotebook — Yjs notebook model |
| `packaging` | PEP 508 parsing and version/extras/marker checks |

The collaboration client libraries are core because the package exposes the
collaborative document transport directly. The server-side
`jupyter-collaboration` extension remains an integration dependency.

### Feature extras

| Extra | Purpose |
|---------|---------|
| `local` | ipykernel for a managed local Python kernel |
| `batch` | nbclient-backed dedicated notebook execution |
| `dataframe` | pandas and Arrow serialization |
| `integration` | Jupyter Server and collaboration test environment |

### Optional scientific stack

| Package | Purpose |
|---------|---------|
| `pandas` + `pyarrow` | Arrow IPC serialization for DataFrames |
| `numpy` | ndarray JSON serialization |
| `Pillow` | PIL.Image PNG serialization |

These are detected at runtime and registered via `mimetypes.register_*_handlers()`.

## Thread Safety and Concurrency

- Shared shell-channel consumers are serialized so replies cannot be stolen by
  another execution or introspection request. Interrupt and lifecycle control
  remain available through their own paths.
- Control-channel requests use a separate lock. Remote WebSocket frames are
  routed to per-request queues by parent message ID, so debugger control does
  not compete with an active execution collector.
- `NotebookSession` serializes multi-step append, source-update, execution, and
  run-all workflows. Cell IDs are captured or resolved in the document mutation
  that needs them.
- `KernelManager` uses an internal `asyncio.Lock` for lifecycle operations
  (start, shutdown, restart, interrupt).
- `LocalFileDocumentTransport` uses an `asyncio.Lock` for file I/O.
- `CollabYjsDocumentTransport` uses an `asyncio.Lock` for cell mutations
  that involve multi-step CRDT operations.
- Output callbacks run sequentially in a separate dispatcher. It keeps the
  newest pending snapshot, applies a configurable timeout, and records
  coalescing or errors on `ExecutionResult`.
- Collaboration output updates mutate existing CRDT fields, and broadcasts use
  state-vector deltas whose baseline advances only after a successful send.

## Extension Points

1. **Custom kernel transports** — subclass `KernelTransport` with custom
   `start()`, `execute()`, etc.
2. **Custom notebook transports** — implement the `NotebookDocumentTransport`
   protocol
3. **MIME type handlers** — call `register_handler()` for custom types
4. **Execution hooks** — use `KernelHooks` for instrumentation
5. **Variable ops** — register templates for non-Python kernels via
   `VariableOpsRegistry`
