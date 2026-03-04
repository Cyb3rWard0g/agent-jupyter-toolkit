# Quickstart

This guide covers two ways to run the MCP Jupyter Notebook server:

| Mode | Where you watch | Transport | Best for |
|------|----------------|-----------|----------|
| **Local** | VS Code notebook editor | File-based `.ipynb` | Quick start, no infra needed |
| **Server** | JupyterLab in the browser | Yjs collab (real-time sync) | Shared / remote notebooks |

Each mode can use either the **published PyPI package** or your **local dev clone**.

---

## Local Mode — for VS Code

No Docker, no Jupyter server. The MCP server spawns its own kernel and writes to a local `.ipynb` file. Open that file in VS Code to watch the agent work.

> **Notebook auto-creation:** If the notebook file doesn't exist yet, the server creates an empty `.ipynb` automatically on first start. No manual setup needed.

### 1. Add the MCP server

Create `.vscode/mcp.json` in your project root:

**From PyPI** (latest published release):

```jsonc
{
  "servers": {
    "mcp-jupyter-notebook": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-jupyter-notebook"],
      "env": {
        "MCP_JUPYTER_SESSION_MODE": "local",
        "MCP_JUPYTER_NOTEBOOK_PATH": "notebooks/agent_demo.ipynb"
      }
    }
  }
}
```

**From local clone** (development — run unreleased code from the repo):

Use this when you've cloned the [agent-jupyter-toolkit](https://github.com/sdan/agent-jupyter-toolkit) repo and want to run the latest source. `uv run --directory` tells uv to resolve the package from your local checkout instead of PyPI.

```jsonc
{
  "servers": {
    "mcp-jupyter-notebook": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "run",
        "--directory", "${workspaceFolder}/packages/mcp-jupyter-notebook",
        "mcp-jupyter-notebook"
      ],
      "env": {
        "MCP_JUPYTER_SESSION_MODE": "local",
        "MCP_JUPYTER_NOTEBOOK_PATH": "${workspaceFolder}/packages/mcp-jupyter-notebook/quickstarts/notebooks/agent_notebook_demo.ipynb"
      }
    }
  }
}
```

> **Why `${workspaceFolder}`?** VS Code expands this to your workspace root. Using an absolute path ensures the notebook ends up in the right place regardless of the server's working directory.

### 2. Start & test

1. **Cmd+Shift+P** → `MCP: List Servers` → select **mcp-jupyter-notebook** → **Start**
2. Ask the agent: *"Create a notebook that generates 100 random numbers and plots a histogram"*
3. Open the notebook file in VS Code — cells appear as the agent adds them

### Exercises (Local Mode)

Try these small prompts to validate the tool surface end-to-end:

1. *"Add a markdown title and a short description of what this notebook does."*
2. *"Create a variable called `data` with 20 random integers, then show it."*
3. *"List all user-defined variables in the kernel."*
4. *"Install `pandas` and show its version."*
5. *"Delete the last cell, then read the notebook to confirm it changed."*

---

## Server Mode — for JupyterLab (Browser)

The notebook lives on a Jupyter server. Edits sync to JupyterLab in real-time via Yjs collab. Open the notebook in your browser to watch.

> **Why not use server mode with VS Code?** VS Code only borrows the remote kernel — the notebook document stays local. The agent's edits on the server won't appear in VS Code. Use local mode instead.

### 1. Start Jupyter

**Option A — Docker (recommended):**

```bash
cd packages/mcp-jupyter-notebook/quickstarts
docker compose up -d --build
```

Opens JupyterLab at **http://localhost:8888** (token: `mcp-dev-token`).

> Want PostgreSQL too? Use the same Compose file with the `postgres` profile:
>
> ```bash
> cp .env.example .env
> docker compose --profile postgres up -d --build
> ```

**Option B — Without Docker:**

```bash
pip install jupyterlab ipykernel jupyter-collaboration
jupyter lab --port 8888 --IdentityProvider.token=mcp-dev-token
```

### 2. Add the MCP server

`.vscode/mcp.json`:

**From PyPI** (latest published release):

```jsonc
{
  "servers": {
    "mcp-jupyter-notebook": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-jupyter-notebook"],
      "env": {
        "MCP_JUPYTER_SESSION_MODE": "server",
        "MCP_JUPYTER_BASE_URL": "http://localhost:8888",
        "MCP_JUPYTER_TOKEN": "mcp-dev-token",
        "MCP_JUPYTER_NOTEBOOK_PATH": "agent_notebook_demo.ipynb"
      }
    }
  }
}
```

**From local clone** (development — latest from source):

```jsonc
{
  "servers": {
    "mcp-jupyter-notebook": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "run",
        "--directory", "${workspaceFolder}/packages/mcp-jupyter-notebook",
        "mcp-jupyter-notebook"
      ],
      "env": {
        "MCP_JUPYTER_SESSION_MODE": "server",
        "MCP_JUPYTER_BASE_URL": "http://localhost:8888",
        "MCP_JUPYTER_TOKEN": "mcp-dev-token",
        "MCP_JUPYTER_NOTEBOOK_PATH": "agent_notebook_demo.ipynb"
      }
    }
  }
}
```

### 3. Start & test

1. **Cmd+Shift+P** → `MCP: List Servers` → select **mcp-jupyter-notebook** → **Start**
2. Ask the agent: *"Create a notebook that generates 100 random numbers and plots a histogram"*
3. Open **http://localhost:8888** in your browser — cells appear in real-time

### Exercises (Server Mode)

These focus on confirming the “edits show up in JupyterLab” workflow:

1. *"Create a new section in the notebook called 'Exploration' and add 3 TODO bullet points."*
2. *"Run a code cell that prints the Python version and the current working directory."*
3. *"Read the notebook and summarize the first 5 cells (type + first line)."*
4. *"Restart the kernel, then re-run a simple cell to confirm the kernel is alive."*

### Exercise (Optional): PostgreSQL tools

Prereqs:
- Start Jupyter + Postgres: `cp .env.example .env` then `docker compose --profile postgres up -d --build`
- Wait for Postgres to be **healthy** (Compose includes a healthcheck). Quick check:

  ```bash
  docker compose --profile postgres ps
  ```

  You should see the `postgres` service as `healthy` before attempting queries.
- Enable tools in your MCP server config: set `MCP_JUPYTER_ENABLE_TOOLS=postgresql`

Notes:
- “Enable tools” just controls **which MCP tools are registered**. It doesn’t connect to Postgres by itself.
- In **server mode**, `PG_DSN` must be set in the **Jupyter container** environment so the kernel inherits it (the Compose `.env` + `docker-compose.yml` do this).

Then ask the agent:

1. *"Use `postgresql_connect` to create a kernel connection named `pg_conn`."*
2. *"Query `SELECT * FROM demo_users ORDER BY id` and display the results."*
3. *"Close the connection with `postgresql_close`."*

### Cleanup

```bash
cd packages/mcp-jupyter-notebook/quickstarts
docker compose down -v
```

---

## Switching Between Dev & PyPI

The `.vscode/mcp.json` in this repo is pre-configured with **both options** as comments. To switch:

| Want to use… | Do this |
|---|---|
| **Dev (local clone)** | Keep Option A active (default). Uses `uv run --directory` to run from source. |
| **PyPI release** | Comment out Option A, uncomment Option B. Uses `uvx` to pull from PyPI. |

To switch between **local** and **server** mode within the same option, swap the env vars as shown in the comments inside [.vscode/mcp.json](../../.vscode/mcp.json).

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MCP_JUPYTER_SESSION_MODE` | `server` | `local` (VS Code) or `server` (JupyterLab) |
| `MCP_JUPYTER_NOTEBOOK_PATH` | Auto-generated | Default notebook path (auto-created if missing in local mode) |
| `MCP_JUPYTER_BASE_URL` | — | Jupyter server URL (server mode only) |
| `MCP_JUPYTER_TOKEN` | — | Jupyter auth token (server mode only) |
| `MCP_JUPYTER_KERNEL_NAME` | `python3` | Kernel spec name |
| `MCP_JUPYTER_TRANSPORT` | `stdio` | MCP transport: `stdio` or `streamable-http` |

### Optional: PostgreSQL tools

To enable the optional PostgreSQL helper tools (`postgresql_connect`, `postgresql_close`):

- Set `MCP_JUPYTER_ENABLE_TOOLS=postgresql`, or
- Start the server with `--enable-tools postgresql`

For **server mode**, the kernel runs on the Jupyter server.
So database env vars (like `PG_DSN`) should be configured in the **Jupyter server environment**.

The easiest path is Docker Compose in this folder:

- Jupyter only: `docker compose up -d --build`
- Jupyter + Postgres: `docker compose --profile postgres up -d --build`
