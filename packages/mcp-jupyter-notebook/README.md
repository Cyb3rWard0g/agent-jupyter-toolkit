# MCP Jupyter Notebook Server

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](../../LICENSE)

A [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server that gives AI agents full control over a live Jupyter notebook session — run code, add markdown, manage packages, inspect variables, and more. Built on [agent-jupyter-toolkit](https://github.com/Cyb3rWard0g/agent-jupyter-toolkit) and the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).

<p align="center">
  <img src="https://img.shields.io/badge/MCP-Tools-green" alt="MCP tools" />
  <img src="https://img.shields.io/badge/transport-stdio%20|%20SSE%20|%20HTTP-orange" alt="transports" />
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+" />
</p>

## Features

- **Rich MCP tool surface** — notebook lifecycle, code execution, markdown, stable cell-ID operations, packages, kernel control, introspection, variables
- **Optional PostgreSQL tools** — enable on demand to create a DB client/connection in the kernel
- **Real-time sync** — Yjs collaboration transport shows cell edits instantly in JupyterLab
- **Two session modes** — connect to a remote Jupyter server or run a local kernel
- **Three transports** — stdio (for editors), SSE, or streamable HTTP
- **Zero config defaults** — sensible defaults with full override via CLI args or env vars

## Quick Start

### 1. Start a Jupyter server

```sh
# With Docker (recommended — includes jupyter-collaboration)
cd quickstarts && docker compose up -d --build

# Or locally
pip install jupyterlab ipykernel jupyter-collaboration
jupyter lab --port 8888 --IdentityProvider.token=mcp-dev-token
```

### 2. Configure your editor

**VS Code** — create `.vscode/mcp.json`:

```json
{
  "servers": {
    "jupyter": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "${workspaceFolder}", "mcp-jupyter-notebook"],
      "env": {
        "MCP_JUPYTER_SESSION_MODE": "server",
        "MCP_JUPYTER_BASE_URL": "http://localhost:8888",
        "MCP_JUPYTER_TOKEN": "mcp-dev-token",
        "MCP_JUPYTER_NOTEBOOK_PATH": "agent_demo.ipynb"
      }
    }
  }
}
```

> See the [quickstart guide](quickstarts/README.md) for Cursor, Claude Desktop, and published (`uvx`) vs local dev configs.

#### Optional: enable PostgreSQL tools

To add database helper tools, set either:

- `MCP_JUPYTER_ENABLE_TOOLS=postgresql` (env var), or
- `--enable-tools postgresql` (CLI flag)

In **server mode**, the kernel runs on the Jupyter server, so database env vars (like `PG_DSN`) must be set in the **Jupyter server environment** (see `quickstarts/docker-compose.yml` with `docker compose --profile postgres`).

Note: enabling tools only controls **tool registration**. Your PostgreSQL service must still be running and ready to accept connections (the quickstart Compose profile includes a healthcheck).

The PostgreSQL tools will use `PG_DSN` / `POSTGRES_DSN` / `DATABASE_URL` (in that order), or fall back to libpq env vars.

### 3. Use it

Ask your agent:

> *"Create a notebook that generates 100 random numbers, plots a histogram, and adds a markdown summary."*

---

## Tools

> Every existing tool accepts an optional `notebook_path` parameter for multi-notebook workflows. When omitted, the default notebook is used.

### Notebook Lifecycle

| Tool | Description |
|---|---|
| `notebook_open` | Open a notebook and create a session (kernel + document transport) |
| `notebook_close` | Close a notebook session and release its resources |
| `notebook_delete` | Delete a notebook file and close its session (server: Contents API, local: filesystem) |
| `notebook_list` | List all currently open notebook sessions |
| `notebook_files_list` | Discover `.ipynb` files on disk (local) or via Contents API (server) |

### Code Execution

| Tool | Description |
|---|---|
| `notebook_code_run` | Append a new code cell, execute it, and return outputs (stdout, stderr, rich displays) |
| `notebook_code_run_existing` | Replace the source of an existing cell (by index) and re-execute it |
| `notebook_code_execute` | Execute code in the kernel *without* creating a notebook cell (background work) |
| `notebook_cells_run` | Execute multiple cells sequentially (code and/or markdown) |
| `notebook_run_all` | Execute every code cell in notebook order and return a per-cell summary |
| `notebook_restart_and_run_all` | Restart the kernel, then execute every code cell from a clean state |

### Notebook Document

| Tool | Description |
|---|---|
| `notebook_markdown_add` | Add a markdown cell (append or insert at position) |
| `notebook_read` | Read all cells, sources, outputs, and metadata |
| `notebook_cell_delete` | Delete a cell by its 0-based index |
| `notebook_cell_delete_by_id` | Delete a cell by its stable Jupyter cell ID |
| `notebook_cell_source_set_by_id` | Replace a cell's source text using its stable cell ID |
| `notebook_cell_move` | Move a cell to a new index using its stable cell ID |
| `notebook_cell_move_before` | Move a cell immediately before another stable cell ID |
| `notebook_cell_move_after` | Move a cell immediately after another stable cell ID |

### Cell Reads

| Tool | Description |
|---|---|
| `notebook_cell_read` | Read a single cell by index (full dict with type, source, outputs, metadata) |
| `notebook_cell_read_by_id` | Read a single cell by stable Jupyter cell ID |
| `notebook_cell_source` | Return only the source text of a cell |
| `notebook_cell_source_by_id` | Return only the source text using a stable cell ID |
| `notebook_cell_count` | Return the number of cells in the notebook |

### Packages

| Tool | Description |
|---|---|
| `notebook_packages_install` | Install Python packages in the kernel (pip-style specifiers) |
| `notebook_packages_check` | Check which packages are available without installing |

### Kernel Control

| Tool | Description |
|---|---|
| `notebook_kernel_interrupt` | Interrupt a running computation (SIGINT) |
| `notebook_kernel_info` | Get kernel metadata (protocol version, language info, banner) |
| `notebook_session_info` | Get session info (kernel type, alive status, connections) |
| `notebook_kernel_history` | Retrieve recent execution history from the kernel |
| `notebook_kernel_restart` | Restart the kernel (destructive — clears all state) |

### Introspection

| Tool | Description |
|---|---|
| `notebook_inspect` | Inspect an object — returns docs, type info, docstrings |
| `notebook_complete` | Get tab-completion suggestions at a cursor position |
| `notebook_code_is_complete` | Check if a code fragment is syntactically complete |
| `notebook_variables_list` | List user-defined variables in the kernel's global scope |
| `notebook_variable_get` | Get the value of a specific variable |
| `notebook_variable_set` | Set a variable in the kernel's global scope |

> Full tool reference with parameters and examples: [docs/tools.md](docs/tools.md)

---

## Installation

```sh
# From PyPI (when published)
uvx mcp-jupyter-notebook

# From PyPI with toolkit DataFrame serialization support
uv pip install "mcp-jupyter-notebook[dataframe]"

# From source
git clone https://github.com/Cyb3rWard0g/mcp-jupyter-notebook.git
cd mcp-jupyter-notebook
uv pip install -e ".[dev]"
```

---

## Configuration

All settings can be passed as **CLI arguments** or **environment variables**. CLI arguments take precedence.

| Variable | CLI Flag | Description | Default |
|---|---|---|---|
| `MCP_JUPYTER_SESSION_MODE` | `--mode` | `server` (remote Jupyter) or `local` | `server` |
| `MCP_JUPYTER_BASE_URL` | `--base-url` | Jupyter server URL | — |
| `MCP_JUPYTER_TOKEN` | `--token` | Jupyter API token | — |
| `MCP_JUPYTER_KERNEL_NAME` | `--kernel-name` | Kernel spec name | `python3` |
| `MCP_JUPYTER_NOTEBOOK_PATH` | `--notebook-path` | Notebook file path (`.ipynb`) | auto-generated |
| `MCP_JUPYTER_TRANSPORT` | `--transport` | `stdio`, `sse`, `streamable-http` | `stdio` |
| `MCP_JUPYTER_HOST` | `--host` | Host for HTTP transports | `127.0.0.1` |
| `MCP_JUPYTER_PORT` | `--port` | Port for HTTP transports | `8000` |
| `MCP_JUPYTER_LOG_LEVEL` | — | `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` |
| `MCP_JUPYTER_HEADERS_JSON` | — | Extra HTTP headers as JSON object | — |
| `MCP_JUPYTER_PREFER_COLLAB` | — | Use Yjs real-time sync (`true`/`false`) | `true` |

> Full configuration reference: [docs/mcp-server/configuration.md](../../docs/mcp-server/configuration.md)

---

## Project Structure

```
src/mcp_jupyter_notebook/
├── __init__.py     # CLI entry point (argparse)
├── server.py       # FastMCP server, lifespan, config processing
├── context.py      # AppContext dataclass (shared lifespan state)
└── tools/          # Tool registrations (notebook + optional domains)
  ├── notebook/       # Core notebook tool pack, split by concern
  └── postgresql.py   # Optional PostgreSQL helper tools
```

---

## Development

```sh
git clone https://github.com/Cyb3rWard0g/mcp-jupyter-notebook.git
cd mcp-jupyter-notebook
uv pip install -e ".[dev]"

# Run tests
uv run pytest

# Lint
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

See [docs/architecture.md](../../docs/architecture.md) for internals and [CONTRIBUTING.md](../../CONTRIBUTING.md) for the release process.

---

## License

[Apache License 2.0](../../LICENSE)
