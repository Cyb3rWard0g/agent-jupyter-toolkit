# Architecture

This document describes the internal structure of the MCP Jupyter Notebook server — how the components fit together, the request lifecycle, and the key design decisions.

---

## Overview

```
┌─────────────┐    stdio/SSE/HTTP    ┌──────────────────────┐
│  MCP Client │◄────────────────────►│  FastMCP Server      │
│  (VS Code,  │                      │  ├─ lifespan         │
│   Cursor,   │                      │  ├─ 27 tools         │
│   Claude)   │                      │  └─ AppContext        │
└─────────────┘                      └──────────┬───────────┘
                                                │
                                     ┌──────────▼───────────┐
                                     │  agent-jupyter-      │
                                     │  toolkit              │
                                     │  ├─ Kernel (WS)      │
                                     │  └─ Doc (Yjs/REST)   │
                                     └──────────┬───────────┘
                                                │
                                     ┌──────────▼───────────┐
                                     │  Jupyter Server      │
                                     │  (JupyterLab)        │
                                     └──────────────────────┘
```

---

## Module Layout

### `__init__.py` — CLI Entry Point

The `main()` function parses CLI arguments with `argparse`, merges them with environment variables via `process_config()`, and runs the async server:

```python
args = parser.parse_args()
config = process_config(args)
asyncio.run(run_server(config))
```

This is the entry point registered in `pyproject.toml`:

```toml
[project.scripts]
mcp-jupyter-notebook = "mcp_jupyter_notebook:main"
```

### `server.py` — Server Core

Contains the FastMCP server creation, configuration processing, session construction, and the lifespan context manager.

**Key functions:**

| Function | Purpose |
|---|---|
| `process_config(args)` | Merges CLI args with env vars (CLI wins). Returns a flat config dict. |
| `_build_session(cfg)` | Constructs a `NotebookSession` from the config — creates kernel and doc transports based on the session mode. |
| `app_lifespan(server)` | Async context manager that starts the `NotebookSession`, yields an `AppContext`, and cleans up on shutdown. |
| `create_server()` | Creates the `FastMCP` instance, attaches the lifespan, and registers all tools. |
| `run_server(cfg)` | Async entry point — sets the global config and runs the server with the configured transport. |

**Configuration priority:** CLI args → environment variables → built-in defaults.

### `context.py` — Shared State

A simple `@dataclass` that holds the `NotebookSession`. This is yielded by the lifespan and available to all tools via the MCP `Context`:

```python
@dataclass
class AppContext:
    session: NotebookSession
```

Tools access it as:

```python
session = ctx.request_context.lifespan_context.session
```

### `tools.py` — Tool Definitions

All 27 MCP tools are registered inside `register_notebook_tools(mcp)`. Each tool is a decorated async function with `ToolAnnotations` describing its behavior:

```python
from mcp.types import ToolAnnotations

@mcp.tool(
    title="Run Code Cell",
    annotations=ToolAnnotations(
        title="Run Code Cell",
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=False,
    ),
)
async def notebook_code_run(code: str, ctx: Context, timeout: float = 120.0) -> dict:
    session = _get_session(ctx)
    result = await invoke_code_cell(session, code, timeout=timeout)
    return _code_result(result)
```

Each tool declares `ToolAnnotations` with hints that help MCP clients understand whether the tool is read-only, destructive, idempotent, or requires open-world access (e.g. network).

Helper functions:

| Helper | Purpose |
|---|---|
| `_get_session(ctx)` | Extracts the `NotebookSession` from the MCP context lifespan |
| `_code_result(result)` | Normalises a `NotebookCodeExecutionResult` into a plain dict for JSON serialization |

---

## Request Lifecycle

1. **MCP client** sends a tool call (e.g. `notebook_code_run` with `code` parameter)
2. **FastMCP** routes the call to the registered tool function
3. **Tool function** extracts the `NotebookSession` from the lifespan context
4. **agent-jupyter-toolkit** executes the operation:
   - **Kernel transport** sends an `execute_request` over WebSocket to the Jupyter kernel
   - **Doc transport** syncs the notebook document (adds/removes cells) via Yjs or REST
5. **Tool function** normalises and returns the result as a JSON dict
6. **FastMCP** serializes the response and sends it back over the MCP transport

---

## Session Construction

The `_build_session()` function creates two transports based on the session mode:

### Server Mode

```python
kernel = create_kernel("remote", base_url=..., token=..., kernel_name=...)
doc = create_notebook_transport("remote", notebook_path, base_url=..., token=..., prefer_collab=True)
```

- **Kernel:** Connects to the Jupyter REST API to create a kernel, then opens a WebSocket for execution
- **Document:** If `prefer_collab=True`, connects via Yjs WebSocket for real-time sync; otherwise uses the Contents REST API

### Local Mode

```python
kernel = create_kernel("local", kernel_name=...)
doc = create_notebook_transport("local", notebook_path, prefer_collab=False)
```

- **Kernel:** Launches a local kernel process via `jupyter_client`
- **Document:** Reads/writes the `.ipynb` file directly on the filesystem

---

## Lifespan Management

The server uses FastMCP's lifespan pattern to manage the `NotebookSession`:

```python
@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    session = _build_session(_server_config)
    await session.start()       # Connect kernel WS, join collab room
    try:
        yield AppContext(session=session)
    finally:
        await session.stop()    # Disconnect WS, leave collab room
```

This ensures:
- The kernel and document transports are fully connected **before** any tools are called
- Cleanup happens gracefully on shutdown (even on errors)
- All tools share a single session instance — no per-request overhead

---

## Dependencies

| Package | Purpose |
|---|---|
| `agent-jupyter-toolkit>=0.2.15` | Kernel management, notebook transport, code execution utilities |
| `mcp>=1.26.0` | MCP Python SDK — `FastMCP`, `Context`, `ToolAnnotations`, transport implementations |

The server delegates all Jupyter-specific logic to `agent-jupyter-toolkit`. The MCP layer is responsible only for:
- Exposing tools with the correct schemas
- Managing the session lifecycle
- Serializing results for the MCP protocol
