# Architecture

This document describes the internal structure of the MCP Jupyter Notebook server — how the components fit together, the request lifecycle, and the key design decisions.

---

## Overview

```
┌─────────────┐    stdio/SSE/HTTP    ┌──────────────────────┐
│  MCP Client │◄────────────────────►│  FastMCP Server      │
│  (VS Code,  │                      │  ├─ lifespan         │
│   Cursor,   │                      │  ├─ notebook tools   │
│   Claude)   │                      │  └─ AppContext        │
└─────────────┘                      └──────────┬───────────┘
                                                │
                                     ┌──────────▼───────────┐
                                     │  agent-jupyter-      │
                                     │  toolkit              │
                                     │  ├─ Workspace        │
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

Contains FastMCP server creation, configuration processing, and the lifespan
context manager. Notebook construction and lifecycle live in the core toolkit.

**Key functions:**

| Function | Purpose |
|---|---|
| `process_config(args)` | Merges CLI args with env vars (CLI wins). Returns a flat config dict. |
| `app_lifespan(server)` | Creates the `SessionManager`, opens the optional default notebook, yields an `AppContext`, and closes every session on shutdown. |
| `create_server()` | Creates the `FastMCP` instance, attaches the lifespan, and registers all tools. |
| `run_server(cfg)` | Async entry point — sets the global config and runs the server with the configured transport. |

**Configuration priority:** CLI args → environment variables → built-in defaults.

### `context.py` — Shared State

`AppContext` holds a core `NotebookWorkspace`. The `SessionManager` compatibility
adapter subclasses it and translates the existing MCP configuration dictionary
into a `NotebookWorkspaceConfig`. Registry and lifecycle behavior live in
`agent_jupyter_toolkit.notebook.workspace`.
It is yielded by the lifespan and available to all tools through the MCP
`Context`:

```python
@dataclass
class AppContext:
    manager: NotebookWorkspace
```

Tools access it as:

```python
manager = ctx.request_context.lifespan_context.manager
session = manager.get(notebook_path)
```

The manager shares concurrent opens for the same canonical path. Close and
delete operations install per-path barriers before transport shutdown starts,
so another open cannot attach to a server kernel during teardown or recreate a
session before file deletion completes.

Workspace shutdown runs as a retained drain task. If its caller is cancelled,
the workspace closes every captured session before delivering that cancellation;
concurrent callers await the same shutdown. Local and Contents API file operations
live behind private workspace file backends. The server backend encodes directory
paths and propagates failed HTTP responses to MCP tool results.

The workspace remembers one default notebook. Opening another notebook does not
switch an existing default unless `set_default=True`; supplying an execution
path overrides the target only for that call. Path normalization also applies
to default selection. Closing the default selects a remaining notebook as soon
as the closing session leaves the registry, before slow transport cleanup.

These defaults are shared by callers using the same workspace. There is no
automatic per-conversation scope. The workspace runs inside the MCP server
process, whether that server is local or remote; clients need no extra SDK or
session-ID argument.

### Tool Definitions

All 43 core MCP tools are registered through `register_notebook_tools(mcp)`.
Each tool is a decorated async function with `ToolAnnotations` describing its
behavior:

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
3. **Tool function** asks the core workspace in the lifespan context to resolve the explicit notebook path or current default
4. **agent-jupyter-toolkit** executes the operation:
   - **Kernel transport** sends an `execute_request` over WebSocket to the Jupyter kernel
   - **Doc transport** syncs the notebook document (adds/removes cells) via Yjs or REST
5. **Tool function** normalises and returns the result as a JSON dict
6. **FastMCP** serializes the response and sends it back over the MCP transport

---

## Session Construction

The core `NotebookWorkspace._build_session()` method creates two transports
based on the session mode:

### Server Mode

```python
kernel = create_kernel(
    "remote", base_url=..., token=..., kernel_name=..., notebook_path=notebook_path
)
doc = create_notebook_transport(
    "remote", notebook_path, base_url=..., token=..., collaboration_mode="preferred"
)
```

- **Kernel:** Reuses or creates the Jupyter Sessions API kernel bound to the same notebook path, then opens its WebSocket channels
- **Document:** Uses required/preferred/disabled collaboration policy. Preferred mode falls back only for classified unsupported API responses and reports the result.

### Local Mode

```python
kernel = create_kernel("local", kernel_name=...)
doc = create_notebook_transport("local", notebook_path, prefer_collab=False)
```

- **Kernel:** Launches a local kernel process via `jupyter_client`
- **Document:** Reads/writes the `.ipynb` file directly on the filesystem

`SessionManager.open()` canonicalizes paths and shares one in-flight startup
task for concurrent requests targeting the same notebook. The session enters
the registry only after both transports start successfully; failed starts clean
up partial resources and remain retryable. Open requests also wait for any
in-flight close or delete of the same path, preventing reuse of a kernel during
teardown and file deletion.

---

## Lifespan Management

The server uses FastMCP's lifespan pattern to manage all notebook sessions:

```python
@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    manager = SessionManager(config=_server_config)
    default_path = _server_config.get("notebook_path")
    if default_path:
        await manager.open(default_path)
    try:
        yield AppContext(manager=manager)
    finally:
        await manager.close_all()
```

This ensures:
- A configured default notebook is connected before any tools are called
- Cleanup happens gracefully on shutdown (even on errors)
- Tools share sessions by canonical notebook path

---

## Dependencies

| Package | Purpose |
|---|---|
| `agent-jupyter-toolkit` | Kernel management, notebook transport, code execution utilities; developed and released with the MCP package |
| `mcp>=1.26.0,<3` | MCP Python SDK — compatibility adapter supports FastMCP 1.26 and MCPServer 2.x |

The server delegates all Jupyter-specific logic to `agent-jupyter-toolkit`. The MCP layer is responsible only for:
- Exposing tools with the correct schemas
- Managing the session lifecycle
- Serializing results for the MCP protocol
