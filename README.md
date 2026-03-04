# Agent Jupyter Toolkit

A project to provide the right tools for AI agents to interact with Jupyter
notebooks. This includes async kernel execution, notebook document management,
and an MCP server exposing these capabilities to any agent framework that supports
MCP clients. The project is organized as a monorepo containing multiple packages.

| Package | Description | Install |
|---------|-------------|---------|
| [**agent-jupyter-toolkit**](packages/agent-jupyter-toolkit/) | Domain library — async kernel sessions, notebook transports, variable management, MIME serialization | `pip install agent-jupyter-toolkit` |
| [**mcp-jupyter-notebook**](packages/mcp-jupyter-notebook/) | MCP server — 28 core notebook tools + 8 optional PostgreSQL tools for AI agents | `pip install mcp-jupyter-notebook` |

## Quick Start

```sh
# Install just the toolkit
pip install agent-jupyter-toolkit

# Or install the MCP server (includes the toolkit as a dependency)
pip install mcp-jupyter-notebook
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

## Development

```sh
git clone https://github.com/Cyb3rWard0g/agent-jupyter-toolkit.git
cd agent-jupyter-toolkit
uv sync --all-packages    # installs both packages + dev deps into .venv
```

```sh
# Run all tests
uv run --all-packages pytest

# Lint & format
uv run ruff check packages/ --fix
uv run black packages/
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
| [MCP Server Docs](docs/mcp-server/) | Server architecture, configuration, 28 notebook + 8 PostgreSQL tools |
| [Contributing](CONTRIBUTING.md) | Dev setup, testing, linting, release process |

## License

[Apache License 2.0](LICENSE)
