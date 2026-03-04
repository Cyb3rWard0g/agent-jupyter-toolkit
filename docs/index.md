# Agent Jupyter Toolkit — Documentation

A monorepo containing two Python packages for building and serving AI agent
tools that interact with Jupyter kernels and notebooks.

| Package | Description | PyPI |
|---------|-------------|------|
| **agent-jupyter-toolkit** | Domain library — kernel execution, notebook transports, utilities | `pip install agent-jupyter-toolkit` |
| **mcp-jupyter-notebook** | MCP server — exposes 27 tools for AI agents via the Model Context Protocol | `pip install mcp-jupyter-notebook` |

---

## Getting Started

| Document | Description |
|----------|-------------|
| [Getting Started](getting-started.md) | Installation, first kernel session, first notebook edit |
| [Architecture](architecture.md) | Monorepo layout, transport pattern, design decisions |
| [Contributing](../CONTRIBUTING.md) | Dev setup, testing, linting, release process |

## Toolkit (`agent-jupyter-toolkit`)

The domain library that provides kernel management, notebook document transports, and high-level utilities.

| Document | Description |
|----------|-------------|
| [Kernel Sessions](toolkit/kernel-sessions.md) | Execution, introspection, interrupts, history, variables |
| [Notebook Transports](toolkit/notebook-transports.md) | Local files, Contents API, collaborative Yjs/CRDT editing |
| [Utilities](toolkit/utilities.md) | High-level helpers, output formatting, package management |
| [Configuration](toolkit/configuration.md) | Environment variables, security allowlists, timeouts |
| [API Reference](toolkit/api-reference.md) | Complete class/function reference with signatures |

## MCP Server (`mcp-jupyter-notebook`)

A thin MCP adapter that wraps the toolkit and exposes it as 27 tools for AI agents.

| Document | Description |
|----------|-------------|
| [Architecture](mcp-server/architecture.md) | Module layout, request lifecycle, lifespan management |
| [Configuration](mcp-server/configuration.md) | CLI args, environment variables, transports, session modes |
| [Tools Reference](mcp-server/tools.md) | All 27 MCP tools with parameters, return values, and examples |

---

## Quick Links

```python
# Minimal kernel execution (using the toolkit directly)
from agent_jupyter_toolkit.kernel import create_session, SessionConfig

async with create_session(SessionConfig(mode="local")) as session:
    result = await session.execute("2 + 2")
    print(result.stdout)  # ""
    print(result.outputs)  # [{output_type: "execute_result", data: {"text/plain": "4"}}]
```

```python
# Minimal notebook session (using the toolkit directly)
from agent_jupyter_toolkit.kernel import create_session, SessionConfig
from agent_jupyter_toolkit.notebook import make_document_transport, NotebookSession

kernel = create_session(SessionConfig(mode="local"))
doc = make_document_transport("local", local_path="analysis.ipynb",
                              remote_base=None, remote_path=None,
                              token=None, headers_json=None,
                              create_if_missing=True)
async with NotebookSession(kernel=kernel, doc=doc) as nb:
    idx, result = await nb.append_and_run("print('hello')")
```

```bash
# Run the MCP server (local mode — no Jupyter server needed)
mcp-jupyter-notebook --mode local --kernel-name python3

# Run the MCP server (server mode — connects to Jupyter)
mcp-jupyter-notebook --mode server --base-url http://localhost:8888 --token my-token
```

## Requirements

- Python 3.11 – 3.13
- A Jupyter kernel (`ipykernel` for Python) for local mode
- A Jupyter Server for remote/collaborative mode
