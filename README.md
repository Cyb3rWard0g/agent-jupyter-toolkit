# Agent Jupyter Toolkit

[![CI](https://github.com/Cyb3rWard0g/agent-jupyter-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/Cyb3rWard0g/agent-jupyter-toolkit/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Cyb3rWard0g/agent-jupyter-toolkit)](https://github.com/Cyb3rWard0g/agent-jupyter-toolkit/releases)
[![License](https://img.shields.io/github/license/Cyb3rWard0g/agent-jupyter-toolkit)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-blue)](https://www.python.org/)

![](docs/Agent-Jupyter-Toolkit-Architecture.png)

An open-source project to provide the right tools for AI agents to execute
code through Jupyter kernels, whether local or remote, and optionally attach
a notebook document for persistent, reproducible workflows. At its core, the
project offers a Python library with async kernel sessions, notebook document
transports, variable management, and dependency tracking so any agent
framework can integrate directly. To align with the community's adoption of
Model Context Protocol (MCP), the project also ships an MCP server that exposes
these same capabilities as a standardized tool suite, including optional
PostgreSQL tools for data workflows.

## Packages

| Package | PyPI | Description |
|---------|------|-------------|
| [**agent-jupyter-toolkit**](packages/agent-jupyter-toolkit/) | [![PyPI version](https://img.shields.io/pypi/v/agent-jupyter-toolkit)](https://pypi.org/project/agent-jupyter-toolkit/) [![Downloads](https://img.shields.io/pypi/dm/agent-jupyter-toolkit)](https://pypi.org/project/agent-jupyter-toolkit/) | Async kernel sessions (local/remote/attach), code execution, notebook transports, cell editing, package management, dependency tracking, variable inspection, and MIME serialization |
| [**mcp-jupyter-notebook**](packages/mcp-jupyter-notebook/) | [![PyPI version](https://img.shields.io/pypi/v/mcp-jupyter-notebook)](https://pypi.org/project/mcp-jupyter-notebook/) [![Downloads](https://img.shields.io/pypi/dm/mcp-jupyter-notebook)](https://pypi.org/project/mcp-jupyter-notebook/) | MCP server — exposes the toolkit as standardized tools for AI agents, plus additional data tools to connect to databases (e.g. PostgreSQL) |

## Quick Start

```sh
# Install the toolkit with a managed local Python kernel
pip install "agent-jupyter-toolkit[local]"

# Install the kernel/document client without a bundled kernel
pip install agent-jupyter-toolkit

# Install toolkit + DataFrame serialization support
pip install agent-jupyter-toolkit[dataframe]

# Or install the MCP server (includes the toolkit as a dependency)
pip install mcp-jupyter-notebook

# MCP server + toolkit DataFrame serialization support
pip install mcp-jupyter-notebook[dataframe]
```

### Toolkit — run code in a Jupyter kernel

```python
import asyncio
from agent_jupyter_toolkit.kernel import SessionConfig, create_session

async def main():
    async with create_session(SessionConfig(mode="local", kernel_name="python3")) as session:
        result = await session.execute("print('Hello from Jupyter!')")
        print(result.stdout)

asyncio.run(main())
```

### MCP Server — expose Jupyter to AI agents

```bash
# stdio transport (for VS Code, Cursor, Claude Desktop)
mcp-jupyter-notebook --mode local --kernel-name python3

# Connect to a remote Jupyter server
mcp-jupyter-notebook --mode server --base-url http://localhost:8888 --token my-token
```

### Enable PostgreSQL tools

The server ships with optional PostgreSQL tools (schema exploration, query→DataFrame).
Enable them with an env var or CLI flag:

```bash
# Env var
export MCP_JUPYTER_ENABLE_TOOLS=postgresql
mcp-jupyter-notebook --mode server --base-url http://localhost:8888 --token my-token

# Or CLI flag
mcp-jupyter-notebook --mode server --enable-tools postgresql ...
```

The kernel reads the database DSN from `PG_DSN`, `POSTGRES_DSN`, or `DATABASE_URL`
(in that order). In server mode these must be set in the **Jupyter server environment**
so kernels inherit them.

A Docker Compose quickstart with Postgres is available:

```bash
cd packages/mcp-jupyter-notebook/quickstarts
docker compose --profile postgres up -d
# Jupyter at http://localhost:8888 (token: mcp-dev-token)
# Postgres seeded with a demo_users table
```

## Azure Monitor Logs / Sentinel notebooks

The optional `azure-monitor` tools follow the PostgreSQL execution model: the
client lives **inside the notebook kernel**, the query is a visible code cell,
and its DataFrame remains available for follow-up analysis.

Install `agent-data-toolkit[azure-monitor]` in the Jupyter kernel environment.
Configure that environment with `AZURE_LOGS_WORKSPACE_ID` and an explicit
`AZURE_LOGS_AUTH=managed-identity` (optionally `AZURE_CLIENT_ID`) or `azure-cli`.
For local Azure CLI authentication, the kernel must have access to the intended
CLI installation and authenticated account; the MCP host's login is not inherited
by a remote kernel.

```sh
mcp-jupyter-notebook --mode server --base-url http://localhost:8888 \
  --token "$JUPYTER_TOKEN" --enable-tools azure-monitor
```

Use `azure_monitor_connect`, `azure_monitor_query_to_df` and `azure_monitor_close`.
The query tool takes an explicit absolute time window, appends a visible KQL
query cell, and returns the cell identity, execution/persistence status and
metadata from that exact retained result. It never runs a second source query
to generate the preview. Opening the same notebook reuses its live client and
variables while its kernel survives.

Source-partial and client-truncated responses are retained, but DataFrame creation
requires explicit `allow_partial=true`. Timeouts/disconnects may leave an unknown
outcome: inspect the retained result before deciding to rerun. This tool does not
automatically retry the notebook query. Azure SDK transport retries are separate.

The runtime's Azure identity is usable by all code in its kernel. These tools do
not enforce an external application's plan allowlist or investigation authority.
Use runtime identity/RBAC and deployment isolation appropriate to that application.
See [the tool reference](docs/mcp-server/azure-monitor.md).

## Development

```sh
git clone https://github.com/Cyb3rWard0g/agent-jupyter-toolkit.git
cd agent-jupyter-toolkit
uv sync --all-packages --all-extras --dev
```

```sh
# Run all tests
uv run --all-packages pytest

# Lint & format
uv run ruff check packages/ --fix
uv run ruff format packages/
```

## Repository Layout

```
├── packages/
│   ├── agent-jupyter-toolkit/    # Domain library
│   │   ├── src/agent_jupyter_toolkit/
│   │   ├── tests/
│   │   └── quickstarts/
│   └── mcp-jupyter-notebook/     # MCP server
│       ├── src/mcp_jupyter_notebook/
│       ├── tests/
│       └── quickstarts/
├── docs/                         # Centralized documentation
│   ├── toolkit/                  # Kernel sessions, transports, utilities, config
│   └── mcp-server/              # Architecture, configuration, tools reference
├── CONTRIBUTING.md               # Dev setup, testing, release process
└── pyproject.toml                # Workspace root (uv workspace config)
```

## Documentation

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Installation, first kernel session, first notebook edit |
| [Architecture](docs/architecture.md) | Monorepo layout, transport pattern, design decisions |
| [Toolkit Docs](docs/toolkit/) | Kernel sessions, notebook transports, utilities, configuration, API reference |
| [MCP Server Docs](docs/mcp-server/) | Server architecture, configuration, notebook + PostgreSQL tools |
| [Contributing](CONTRIBUTING.md) | Dev setup, testing, linting, release process |

## License

[Apache License 2.0](LICENSE)
