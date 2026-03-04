# Agent Jupyter Toolkit

A Python toolkit for building agent tools that interact with Jupyter kernels and the Jupyter protocol. This package provides high-level wrappers and abstractions around [jupyter_client](https://pypi.org/project/jupyter-client/), [jupyter_ydoc](https://pypi.org/project/jupyter-ydoc/), and [pycrdt](https://pypi.org/project/pycrdt/), making it easy to manage kernels, execute code, and build agent-driven workflows for automation, orchestration, and integration with Model Context Protocol (MCP) servers.

## Features

### Kernel

- **Async-first kernel sessions** with pluggable transports:
  - **Local transport** — direct ZMQ communication with local kernel processes
  - **Server transport** — HTTP REST + WebSocket to remote Jupyter servers
- **Code execution** with real-time streaming output callbacks
- **Kernel introspection** — tab-completion, object inspection, code-completeness checks, and execution history retrieval
- **Kernel control** — interrupt running cells, restart kernels, query kernel metadata (language, version, protocol)
- **Variable management** — inspect and set kernel variables safely (base64-encoded payloads)
- **Extensible hooks** — pre/post execution and output hooks for instrumentation

### Notebook

- **Multiple notebook transports:**
  - **Local file transport** — filesystem-backed notebooks with thread-safe read/write
  - **Contents API transport** — Jupyter Server-managed notebooks via REST
  - **Collaboration transport** — real-time multi-client editing via Yjs/CRDT (pycrdt + jupyter_ydoc)
- **Cell operations** — append, insert, delete, set source, update outputs (full replace and delta)
- **In-memory notebook buffer** for staged edits with explicit commit
- **Local notebook autosave** with optional debounced writes
- **Change observers** — register callbacks for cell mutations, saves, and awareness events

## Use Cases

- Build agent tools that execute code in Jupyter kernels
- Integrate Jupyter kernel execution into MCP servers or other orchestration systems
- Automate notebook and code execution for data science, ML, and automation pipelines
- Build collaborative notebook editing experiences with CRDT-based conflict resolution

## Installation

### Install from PyPI

```sh
# Using pip
pip install agent-jupyter-toolkit

# Or using uv
uv pip install agent-jupyter-toolkit
```

### Development (monorepo)

```sh
git clone https://github.com/Cyb3rWard0g/agent-jupyter-toolkit.git
cd agent-jupyter-toolkit
uv sync --all-packages
```

This installs both `agent-jupyter-toolkit` and `mcp-jupyter-notebook` in
editable mode into the `.venv`.

## Quickstart

Local kernel execution does not require a Jupyter Server. The library starts a
local kernel process for you (via `jupyter_client`) and connects over ZMQ.

### Basic execution

```python
import asyncio
from agent_jupyter_toolkit.kernel import SessionConfig, create_session

async def main() -> None:
    async with create_session(SessionConfig(mode="local", kernel_name="python3")) as session:
        result = await session.execute(
            "import os\n"
            "from platform import node\n"
            "user = os.environ.get('USER', 'friend')\n"
            "print(f'Hello {user} from {node()}')\n"
        )
    print("status:", result.status)
    print("stdout:", result.stdout.strip())

asyncio.run(main())
```

### Introspection and control

```python
async with create_session() as session:
    # Tab-completion
    result = await session.complete("import os; os.path.jo", cursor_pos=21)
    print(result.matches)  # ["join"]

    # Object inspection
    info = await session.inspect("len", cursor_pos=3)
    print(info.data.get("text/plain", ""))

    # Code-completeness check
    check = await session.is_complete("for i in range(10):")
    print(check.status)  # "incomplete"
    print(check.indent)  # "    "

    # Execution history
    hist = await session.history(n=5)
    for entry in hist.history:
        print(f"[{entry.line_number}] {entry.input}")

    # Kernel metadata
    kinfo = await session.kernel_info()
    print(f"{kinfo.language_info['name']} {kinfo.language_info['version']}")

    # Interrupt a long-running cell
    import asyncio
    task = asyncio.create_task(session.execute("import time; time.sleep(999)"))
    await asyncio.sleep(1)
    await session.interrupt()
```

For server-backed and notebook scenarios, see the [quickstarts/](quickstarts/) directory.

## API Overview

### Kernel Session

| Method | Description |
|---|---|
| `execute(code, *, timeout, output_callback, ...)` | Execute code with optional streaming callbacks |
| `interrupt()` | Send SIGINT to cancel running execution |
| `complete(code, cursor_pos)` | Tab-completion suggestions |
| `inspect(code, cursor_pos, detail_level)` | Object documentation/signature |
| `is_complete(code)` | Syntax completeness check |
| `history(*, output, raw, hist_access_type, n)` | Execution history retrieval |
| `kernel_info()` | Kernel metadata (language, version, protocol) |
| `is_alive()` | Health check |
| `start()` / `shutdown()` | Lifecycle management (also via `async with`) |

### Notebook Document Transport

| Method | Description |
|---|---|
| `fetch()` | Get notebook content as nbformat dict |
| `save(content)` | Write notebook content |
| `append_code_cell(source, metadata, tags)` | Append a code cell |
| `insert_code_cell(index, source, metadata, tags)` | Insert a code cell at index |
| `append_markdown_cell(source, tags)` | Append a markdown cell |
| `insert_markdown_cell(index, source, tags)` | Insert a markdown cell at index |
| `set_cell_source(index, source)` | Update cell source text |
| `update_cell_outputs(index, outputs, execution_count)` | Replace cell outputs |
| `delete_cell(index)` | Delete cell at index |
| `on_change(callback)` | Register mutation observer |

## Architecture

```
agent_jupyter_toolkit
├── kernel/
│   ├── session.py          # Session + create_session() factory
│   ├── transport.py        # KernelTransport protocol
│   ├── types.py            # Dataclasses (SessionConfig, ExecutionResult, ...)
│   ├── manager.py          # KernelManager (lifecycle, channels)
│   ├── messages.py         # Wire-protocol message builders
│   ├── variables.py        # VariableManager (inspect/set kernel vars)
│   ├── hooks.py            # Extensible execution hooks
│   ├── mimetypes.py        # MIME-type serialization registry
│   ├── serialization.py    # High-level serialize/deserialize API
│   └── transports/
│       ├── local.py        # LocalTransport (ZMQ)
│       └── server.py       # ServerTransport (HTTP+WS)
├── notebook/
│   ├── session.py          # NotebookSession (kernel + document orchestration)
│   ├── transport.py        # NotebookDocumentTransport protocol
│   ├── factory.py          # make_document_transport() factory
│   ├── buffer.py           # In-memory NotebookBuffer
│   ├── cells.py            # Cell creation helpers (code, markdown)
│   ├── config.py           # Environment-based Config (allowlist, timeout)
│   ├── utils.py            # Path validation, output normalization
│   └── transports/
│       ├── local_file.py   # LocalFileDocumentTransport (+ autosave)
│       ├── contents.py     # ContentsApiDocumentTransport
│       └── collab/         # CollabYjsDocumentTransport (Yjs/CRDT)
└── utils/                  # High-level helpers (factories, execution, packages)
```

## Dependencies

| Package | Purpose |
|---|---|
| [jupyter-client](https://pypi.org/project/jupyter-client/) | Kernel protocol, ZMQ channels, kernel management |
| [jupyter-ydoc](https://pypi.org/project/jupyter-ydoc/) | YNotebook schema for collaborative editing |
| [pycrdt](https://pypi.org/project/pycrdt/) | CRDT types (Doc, Array, Map, Text, Awareness) |
| [nbformat](https://pypi.org/project/nbformat/) | Notebook file format handling |
| [aiohttp](https://pypi.org/project/aiohttp/) | Async HTTP/WS for server transport |
| [pandas](https://pypi.org/project/pandas/) + [pyarrow](https://pypi.org/project/pyarrow/) | DataFrame variable inspection |

## Contributing

See [CONTRIBUTING.md](../../CONTRIBUTING.md) for dev setup, testing, and the release process.

---

Contributions and feedback are welcome!