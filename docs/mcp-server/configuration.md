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
  --notebook-path analysis.ipynb \
  --collaboration-mode preferred
```

**Requirements:**
- A running Jupyter server (`jupyter lab`, `jupyter notebook`, or JupyterHub)
- An API token or password for authentication
- (Recommended) `jupyter-collaboration` installed on the server for real-time Yjs sync

### Local Mode

Starts a local kernel process directly — no Jupyter server needed. The notebook is saved to the local filesystem.

```sh
pip install "mcp-jupyter-notebook[local]"
mcp-jupyter-notebook --mode local --kernel-name python3
```

To require encrypted local kernel channels, start the server with:

```sh
mcp-jupyter-notebook --mode local --transport-encryption required
```

Both `required` and `auto` need a Curve-capable libzmq. `required` also refuses
to launch kernelspecs that omit `curve` from `metadata.supported_encryption`.
`auto` uses encryption for kernelspecs that advertise it. `notebook_session_info`
reports the configured `transport_encryption` and whether `encryption_enabled`
is active. This setting applies to local kernel channels. In server mode, use
an HTTPS Jupyter base URL for HTTP and WebSocket transport encryption.

---

## Environment Variables

All configuration can be set via environment variables. This is the recommended approach for editor integrations where you configure the MCP server in a JSON file.

| Variable | Description | Default |
|---|---|---|
| `MCP_JUPYTER_SESSION_MODE` | Session mode: `server` or `local` | `server` |
| `MCP_JUPYTER_BASE_URL` | Jupyter server URL (required in `server` mode) | — |
| `MCP_JUPYTER_TOKEN` | Jupyter API token (required in `server` mode) | — |
| `MCP_JUPYTER_KERNEL_NAME` | Kernel spec name | `python3` |
| `MCP_JUPYTER_TRANSPORT_ENCRYPTION` | Local kernel CurveZMQ policy (`disabled`, `auto`, or `required`) | `disabled` |
| `MCP_JUPYTER_NOTEBOOK_PATH` | Path for the notebook file (`.ipynb`). If omitted, a random name is generated (`mcp_<hash>.ipynb`) | auto-generated |
| `MCP_JUPYTER_DATASET_ROOT` | Private persistent storage writable by the MCP server. Setting it enables the dataset tools | — |
| `MCP_JUPYTER_KERNEL_DATASET_ROOT` | Absolute path to the same storage from the notebook kernel | MCP server's dataset root |
| `MCP_JUPYTER_TRANSPORT` | MCP transport protocol | `stdio` |
| `MCP_JUPYTER_HOST` | Bind host for HTTP transports | `127.0.0.1` |
| `MCP_JUPYTER_PORT` | Bind port for HTTP transports | `8000` |
| `MCP_JUPYTER_LOG_LEVEL` | Python log level | `INFO` |
| `MCP_JUPYTER_HEADERS_JSON` | Extra HTTP headers as a JSON object (e.g. cookies, XSRF tokens) | — |
| `MCP_JUPYTER_PREFER_COLLAB` | Enable Yjs collaboration transport for real-time notebook sync | `true` |
| `MCP_JUPYTER_COLLABORATION_MODE` | Collaboration policy: `required`, `preferred`, or `disabled` | `preferred` |
| `MCP_JUPYTER_ENABLE_TOOLS` | Optional tool sets to enable (comma-separated). Example: `postgresql` | — |

---

## CLI Arguments

```
mcp-jupyter-notebook [OPTIONS]

  --mode MODE             Session mode: server | local
  --base-url URL          Jupyter server URL
  --token TOKEN           Jupyter API token
  --kernel-name NAME      Kernel spec name (default: python3)
  --transport-encryption  Local CurveZMQ policy: disabled | auto | required
  --notebook-path PATH    Notebook file path (.ipynb)
  --dataset-root PATH     Enable saved datasets with this private persistent directory
  --kernel-dataset-root PATH  Same dataset storage as seen by the kernel
  --transport TRANSPORT   stdio | sse | streamable-http
  --collaboration-mode    required | preferred | disabled
  --host HOST             Host for HTTP transports (default: 127.0.0.1)
  --port PORT             Port for HTTP transports (default: 8000)
  --enable-tools TOOLS    Enable optional tool sets (repeatable or comma-separated)
```

CLI arguments override the equivalent environment variable when both are set.

---

## Optional Tool Sets

The server registers the core notebook tools by default.

To enable additional tool domains (for example PostgreSQL helper tools), set:

- `MCP_JUPYTER_ENABLE_TOOLS=postgresql`, or
- `--enable-tools postgresql`

Azure Monitor Logs / Sentinel uses `--enable-tools azure-monitor` or
`MCP_JUPYTER_ENABLE_TOOLS=azure-monitor`. Install the optional connector in the
kernel environment and configure its `AZURE_LOGS_*` settings there. See
[Azure Monitor tools](azure-monitor.md) for identity, query and partial-result behavior.

### Important: where database env vars live

In **server mode**, the kernel runs on the **Jupyter server**, not inside the MCP server process.
That means database environment variables (like `PG_DSN`) must be configured in the **Jupyter server/container environment** so kernels inherit them.

Use the Docker Compose quickstart in `packages/mcp-jupyter-notebook/quickstarts/docker-compose.yml` with `docker compose --profile postgres` to get a working Postgres + Jupyter setup.

---

## Saved Notebook Datasets

Install `mcp-jupyter-notebook[dataframe]` in the MCP server environment and
pandas with PyArrow in the Python kernel. Set `MCP_JUPYTER_DATASET_ROOT` to a
private persistent directory. Give the MCP server write access and the kernel
read access through the same service identity or a private shared group. With
multiple MCP processes, use a filesystem that supports shared file locks and
atomic renames.

When building the MCP server Docker image for this mode, pass
`--build-arg MCP_INSTALL_EXTRAS=dataframe` along with its normal version build
argument. The quickstart Jupyter image includes pandas and PyArrow for the
kernel-side loader.

In local mode, the writer and kernel usually see the same absolute path. If
they use different mounts, configure both paths. For example, with the
quickstart Compose Jupyter service and an MCP process on the host, run from
`packages/mcp-jupyter-notebook/quickstarts` with these settings:

```sh
export MCP_JUPYTER_DATASET_ROOT="$PWD/jupyter/notebooks/datasets"
export MCP_JUPYTER_KERNEL_DATASET_ROOT=/home/jovyan/work/datasets
```

The writer stores one Zstandard-compressed Parquet file at
`<dataset-root>/<notebook-id>/<dataset-id>.parquet`. Jupyter records the notebook
ID in its metadata, so a rename preserves its saved datasets. Each loader uses
the kernel-visible mount path. Keep the `.ipynb` and its dataset directory for
replay, and keep that mount path stable. Copies of the notebook that preserve
its metadata reference the same saved datasets.

Use a storage root scoped to the trusted notebook workspace. The service
retains committed snapshots and keeps unfinished batches in `.pending` for
retry. Include both in the workspace's retention policy. Remove abandoned
staging directories after captures are no longer eligible for retry. Deleting
a notebook file leaves its datasets on storage for that retention policy.

The [`notebook_dataset_*` tools](tools.md#saved-notebook-datasets-optional)
accept rows from a trusted caller. The caller still controls how it obtains
source data. `notebook_import_table` remains available for in-memory imports.

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

When collaboration is required or preferred, the server uses the Yjs WebSocket protocol provided by [`jupyter-collaboration`](https://github.com/jupyterlab/jupyter-collaboration) to sync notebook changes. This means:

- Cell edits appear **instantly** in JupyterLab in your browser
- Multiple agents or users can edit the same notebook simultaneously
- The notebook document is kept in sync via Conflict-free Replicated Data Types (CRDTs)

**Requirements:** The Jupyter server must have `jupyter-collaboration>=5.0.0` installed for collaboration mode. The Docker setup in `quickstarts/` includes this by default.

- `required` uses collaboration and preserves any startup failure.
- `preferred` falls back to the Contents API only when the collaboration endpoint reports an unsupported response. Permission, authentication, and transient failures remain errors.
- `disabled` uses the Contents API directly.

`MCP_JUPYTER_PREFER_COLLAB=true/false` remains a compatibility alias for
preferred/disabled when `MCP_JUPYTER_COLLABORATION_MODE` is unset. The explicit
mode takes precedence.

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
