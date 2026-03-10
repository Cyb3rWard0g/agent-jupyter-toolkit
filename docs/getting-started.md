# Getting Started

This guide covers installation for both packages in this monorepo.

## Installation

### From PyPI

```bash
# Install the toolkit (domain library)
pip install agent-jupyter-toolkit

# Install optional DataFrame serialization support
pip install agent-jupyter-toolkit[dataframe]

# Install the MCP server (also pulls in the toolkit as a dependency)
pip install mcp-jupyter-notebook

# Install the MCP server with the toolkit's optional DataFrame support
pip install mcp-jupyter-notebook[dataframe]
```

### Development (editable)

```bash
git clone https://github.com/Cyb3rWard0g/agent-jupyter-toolkit.git
cd agent-jupyter-toolkit
uv sync --all-packages
```

This installs both `agent-jupyter-toolkit` and `mcp-jupyter-notebook` in
editable mode into the `.venv`, along with all dev dependencies.

## Prerequisites

| Dependency | When needed |
|-----------|-------------|
| `ipykernel` | Local kernel execution |
| `jupyter-server` | Remote kernel / Contents API transport |
| `jupyter-collaboration` | Collaborative Yjs/CRDT notebook transport |

For local-only usage, `ipykernel` is usually already installed. For remote
scenarios, start a Jupyter Server:

```bash
uv pip install jupyter-server ipykernel
TOKEN="$(python -c "import uuid; print(uuid.uuid4())")"
echo "TOKEN=$TOKEN"
jupyter server --port 8888 --IdentityProvider.token "$TOKEN"
```

## Your First Kernel Session

The simplest workflow is executing code in a local kernel:

```python
import asyncio
from agent_jupyter_toolkit.kernel import create_session, SessionConfig

async def main():
    config = SessionConfig(mode="local", kernel_name="python3")

    async with create_session(config) as session:
        result = await session.execute("2 + 2")
        print(f"Status: {result.status}")           # "ok"
        print(f"Outputs: {result.outputs}")          # execute_result with "4"
        print(f"Execution count: {result.execution_count}")

asyncio.run(main())
```

### What happened

1. `create_session()` chose `LocalTransport` because `mode="local"`
2. `session.start()` (called by `__aenter__`) spawned a local IPython kernel
   and connected via ZMQ
3. `session.execute()` sent code to the kernel and collected IOPub outputs
4. `session.shutdown()` (called by `__aexit__`) shut down the kernel process

## Your First Remote Session

Connect to a running Jupyter Server instead of spawning a local kernel:

```python
import asyncio
from agent_jupyter_toolkit.kernel import create_session, SessionConfig, ServerConfig

async def main():
    config = SessionConfig(
        mode="server",
        server=ServerConfig(
            base_url="http://localhost:8888",
            token="YOUR_TOKEN",
        ),
    )
    async with create_session(config) as session:
        result = await session.execute("import sys; print(sys.version)")
        print(result.stdout)

asyncio.run(main())
```

## Your First Notebook Edit

Combine a kernel session with a notebook document transport to persist
execution results into an `.ipynb` file:

```python
import asyncio
from agent_jupyter_toolkit.kernel import create_session, SessionConfig
from agent_jupyter_toolkit.notebook import make_document_transport, NotebookSession

async def main():
    kernel = create_session(SessionConfig(mode="local"))
    doc = make_document_transport(
        "local",
        local_path="output/my_notebook.ipynb",
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
        create_if_missing=True,
    )

    async with NotebookSession(kernel=kernel, doc=doc) as nb:
        # Add a markdown header
        await nb.run_markdown("# My Analysis")

        # Execute code — outputs stream to the notebook in real time
        idx, result = await nb.append_and_run(
            "import pandas as pd\n"
            "df = pd.DataFrame({'x': [1,2,3], 'y': [4,5,6]})\n"
            "df"
        )
        print(f"Cell {idx}: {result.status}")

asyncio.run(main())
```

After running this, `output/my_notebook.ipynb` contains a markdown cell and a
code cell with the DataFrame output.

## Using the Utility Helpers

For even simpler usage, the `utils` module provides one-call functions:

```python
import asyncio
from agent_jupyter_toolkit.utils import create_kernel, execute_code

async def main():
    kernel = create_kernel("local")
    async with kernel:
        result = await execute_code(kernel, "sum(range(100))")
        print(result.formatted_output)  # "4950"
        print(result.status)            # "ok"

asyncio.run(main())
```

## Using the MCP Server

The `mcp-jupyter-notebook` package exposes a Model Context Protocol server
that wraps the toolkit for use with LLM agents (Claude Desktop, VS Code
Copilot, etc.):

```bash
# Run the server (stdio transport, the default)
mcp-jupyter-notebook

# Or with explicit options
mcp-jupyter-notebook --mode local --kernel-name python3
```

### Enable PostgreSQL tools

The MCP server includes optional PostgreSQL tools for schema exploration and
query→DataFrame workflows. Enable them with:

```bash
# Env var
export MCP_JUPYTER_ENABLE_TOOLS=postgresql
mcp-jupyter-notebook --mode server --base-url http://localhost:8888 --token my-token

# Or CLI flag
mcp-jupyter-notebook --enable-tools postgresql --mode server ...
```

The PostgreSQL tools need a database DSN available inside the **Jupyter kernel**.
In server mode, set `PG_DSN` (or `POSTGRES_DSN` / `DATABASE_URL`) in the Jupyter
server’s environment so kernels inherit it.

#### Quickstart with Docker Compose

The quickstarts directory includes a ready-made Compose setup:

```bash
cd packages/mcp-jupyter-notebook/quickstarts
docker compose --profile postgres up -d
```

This starts:
- **Jupyter** at `http://localhost:8888` (token: `mcp-dev-token`)
- **PostgreSQL** with a seeded `demo_users` table (alice, bob, carol)

Then configure your editor’s MCP client:

```jsonc
// .vscode/mcp.json (VS Code example)
{
  "servers": {
    "mcp-jupyter-notebook": {
      "type": "stdio",
      "command": "mcp-jupyter-notebook",
      "env": {
        "MCP_JUPYTER_SESSION_MODE": "server",
        "MCP_JUPYTER_BASE_URL": "http://localhost:8888",
        "MCP_JUPYTER_TOKEN": "mcp-dev-token",
        "MCP_JUPYTER_NOTEBOOK_PATH": "agent_notebook_demo.ipynb",
        "MCP_JUPYTER_ENABLE_TOOLS": "postgresql"
      }
    }
  }
}
```

Now ask your agent: *“List the database schemas and tables, then query demo_users into a DataFrame.”*

See the [MCP Server docs](mcp-server/architecture.md) for full configuration
and the [Tools reference](mcp-server/tools.md) for all exposed tools.

## Next Steps

### Toolkit

- [Kernel Sessions](toolkit/kernel-sessions.md) — introspection, interrupts, variables, hooks
- [Notebook Transports](toolkit/notebook-transports.md) — local files, remote, collaboration
- [Utilities](toolkit/utilities.md) — output formatting, package management
- [Configuration](toolkit/configuration.md) — environment variables and security

### MCP Server

- [Architecture](mcp-server/architecture.md) — server design and request flow
- [Configuration](mcp-server/configuration.md) — environment variables, client setup, optional tool sets
- [Tools Reference](mcp-server/tools.md) — 28 notebook tools + 8 PostgreSQL tools
