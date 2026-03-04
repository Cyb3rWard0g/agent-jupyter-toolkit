# Configuration

The MCP Jupyter Notebook server is configured through **CLI arguments** and/or **environment variables**. When both are provided, CLI arguments take precedence.

---

## Session Modes

The server supports two session modes that determine how it connects to a Jupyter kernel.

### Server Mode (default)

Connects to an existing Jupyter server over HTTP. The server must be running and reachable at the configured base URL. This is the standard mode for editor integrations.

```sh
mcp-jupyter-notebook \
  --mode server \
  --base-url http://localhost:8888 \
  --token my-token \
  --notebook-path analysis.ipynb
```

**Requirements:**
- A running Jupyter server (`jupyter lab`, `jupyter notebook`, or JupyterHub)
- An API token or password for authentication
- (Recommended) `jupyter-collaboration` installed on the server for real-time Yjs sync

### Local Mode

Starts a local kernel process directly — no Jupyter server needed. The notebook is saved to the local filesystem.

```sh
mcp-jupyter-notebook --mode local --kernel-name python3
```

**Requirements:**
- `ipykernel` installed in the environment
- A kernel spec registered (e.g. `python3`)

---

## Environment Variables

All configuration can be set via environment variables. This is the recommended approach for editor integrations where you configure the MCP server in a JSON file.

| Variable | Description | Default |
|---|---|---|
| `MCP_JUPYTER_SESSION_MODE` | Session mode: `server` or `local` | `server` |
| `MCP_JUPYTER_BASE_URL` | Jupyter server URL (required in `server` mode) | — |
| `MCP_JUPYTER_TOKEN` | Jupyter API token (required in `server` mode) | — |
| `MCP_JUPYTER_KERNEL_NAME` | Kernel spec name | `python3` |
| `MCP_JUPYTER_NOTEBOOK_PATH` | Path for the notebook file (`.ipynb`). If omitted, a random name is generated (`mcp_<hash>.ipynb`) | auto-generated |
| `MCP_JUPYTER_TRANSPORT` | MCP transport protocol | `stdio` |
| `MCP_JUPYTER_HOST` | Bind host for HTTP transports | `127.0.0.1` |
| `MCP_JUPYTER_PORT` | Bind port for HTTP transports | `8000` |
| `MCP_JUPYTER_LOG_LEVEL` | Python log level | `INFO` |
| `MCP_JUPYTER_HEADERS_JSON` | Extra HTTP headers as a JSON object (e.g. cookies, XSRF tokens) | — |
| `MCP_JUPYTER_PREFER_COLLAB` | Enable Yjs collaboration transport for real-time notebook sync | `true` |
| `MCP_JUPYTER_ENABLE_TOOLS` | Optional tool sets to enable (comma-separated). Example: `postgresql` | — |

---

## CLI Arguments

```
mcp-jupyter-notebook [OPTIONS]

  --mode MODE             Session mode: server | local
  --base-url URL          Jupyter server URL
  --token TOKEN           Jupyter API token
  --kernel-name NAME      Kernel spec name (default: python3)
  --notebook-path PATH    Notebook file path (.ipynb)
  --transport TRANSPORT   stdio | sse | streamable-http
  --host HOST             Host for HTTP transports (default: 127.0.0.1)
  --port PORT             Port for HTTP transports (default: 8000)
  --enable-tools TOOLS    Enable optional tool sets (repeatable or comma-separated)
```

CLI arguments override the equivalent environment variable when both are set.

---

## Optional Tool Sets

The server always registers the notebook tools.

To enable additional tool domains (for example PostgreSQL helper tools), set:

- `MCP_JUPYTER_ENABLE_TOOLS=postgresql`, or
- `--enable-tools postgresql`

### Important: where database env vars live

In **server mode**, the kernel runs on the **Jupyter server**, not inside the MCP server process.
That means database environment variables (like `PG_DSN`) must be configured in the **Jupyter server/container environment** so kernels inherit them.

Use the Docker Compose quickstart in `packages/mcp-jupyter-notebook/quickstarts/docker-compose.yml` with `docker compose --profile postgres` to get a working Postgres + Jupyter setup.

---

## Transports

The server supports three MCP transport protocols.

### stdio (default)

Standard input/output — the protocol used by VS Code, Cursor, Claude Desktop, and most MCP clients. The MCP client launches the server as a subprocess and communicates over stdin/stdout.

```sh
mcp-jupyter-notebook --transport stdio
```

### SSE (Server-Sent Events)

The server starts an HTTP server and the MCP client connects to it via SSE. Useful for remote or containerized deployments.

```sh
mcp-jupyter-notebook --transport sse --host 0.0.0.0 --port 8000
```

### Streamable HTTP

HTTP-based transport with bidirectional streaming. Similar to SSE but uses newer HTTP streaming semantics.

```sh
mcp-jupyter-notebook --transport streamable-http --host 0.0.0.0 --port 8000
```

---

## Collaboration Transport

When `MCP_JUPYTER_PREFER_COLLAB` is `true` (the default), the server uses the Yjs WebSocket protocol provided by [`jupyter-collaboration`](https://github.com/jupyterlab/jupyter-collaboration) to sync notebook changes. This means:

- Cell edits appear **instantly** in JupyterLab in your browser
- Multiple agents or users can edit the same notebook simultaneously
- The notebook document is kept in sync via Conflict-free Replicated Data Types (CRDTs)

**Requirements:** The Jupyter server must have `jupyter-collaboration>=4.1.1` installed. The Docker setup in `quickstarts/` includes this by default.

If `jupyter-collaboration` is not available, set `MCP_JUPYTER_PREFER_COLLAB=false` to fall back to the standard Contents API (REST-based save/load).

---

## Extra Headers

Some Jupyter deployments (e.g. JupyterHub, enterprise proxies) require additional HTTP headers such as XSRF tokens or cookies. Pass them as a JSON object:

```sh
export MCP_JUPYTER_HEADERS_JSON='{"X-XSRFToken": "abc123", "Cookie": "session=xyz"}'
mcp-jupyter-notebook
```

Or in your editor's MCP config:

```json
{
  "env": {
    "MCP_JUPYTER_HEADERS_JSON": "{\"X-XSRFToken\": \"abc123\"}"
  }
}
```

---

## Priority Order

Configuration is resolved in this order (first match wins):

1. **CLI arguments** (`--mode server`)
2. **Environment variables** (`MCP_JUPYTER_SESSION_MODE=server`)
3. **Built-in defaults** (`server`, `python3`, `stdio`, etc.)
