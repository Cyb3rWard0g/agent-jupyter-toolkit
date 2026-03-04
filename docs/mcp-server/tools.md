# Tools Reference

The MCP Jupyter Notebook server exposes **28 core notebook tools** organized into eight categories, plus **8 optional PostgreSQL tools** for database exploration and query→DataFrame workflows. All tools are registered via `@mcp.tool()` with `ToolAnnotations` and accessible through any MCP client.

> **Multi-notebook support:** Every tool accepts an optional `notebook_path` parameter. When omitted, the tool targets the default notebook (backward compatible with single-notebook setups).

> **PostgreSQL tools:** Enable with `MCP_JUPYTER_ENABLE_TOOLS=postgresql` or `--enable-tools postgresql`. See [Configuration](configuration.md) for details.

### Tool Annotations

Every tool declares `ToolAnnotations` that describe its behavior to MCP clients:

| Hint | Meaning |
|------|---------|
| `read_only_hint` | Tool does not modify state (safe to call speculatively) |
| `destructive_hint` | Tool may irreversibly destroy data (e.g. delete cell, restart kernel) |
| `idempotent_hint` | Calling the tool multiple times with the same input produces the same result |
| `open_world_hint` | Tool accesses external resources (network, PyPI, etc.) |

---

## Notebook Lifecycle

### `notebook_open`

Open a notebook and create a session for it. If the notebook is already open the existing session is returned.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `notebook_path` | `string` | Yes | — | Path to the notebook file |
| `set_default` | `bool` | No | `false` | Make this the default notebook for tools that omit `notebook_path` |

**Returns:** `ok`, `notebook_path`, `is_default`, `open_notebooks`

**Example prompt:** *"Open analysis.ipynb"*

---

### `notebook_close`

Close a notebook session and release its resources. Shuts down the kernel and disconnects the document transport.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `notebook_path` | `string` | Yes | — | Path to the notebook to close |

**Returns:** `ok`, `notebook_path`, `default_notebook`, `open_notebooks`

**Example prompt:** *"Close the data-cleaning notebook"*

---

### `notebook_list`

List all currently open notebook sessions with default status.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| *(none)* | — | — | — | — |

**Returns:** `ok`, `default_notebook`, `notebooks` (list with `notebook_path` and `is_default`)

**Example prompt:** *"Which notebooks are open?"*

---

### `notebook_files_list`

Discover `.ipynb` files available for opening. In local mode scans the filesystem; in server mode queries the Jupyter Contents API.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `directory` | `string` | No | `"."` | Root directory to search |
| `recursive` | `bool` | No | `false` | Search sub-directories |

**Returns:** `ok`, `directory`, `recursive`, `notebooks` (list with `path`, `name`, `is_open`)

**Example prompt:** *"What notebooks are in this project?"*

---

### `notebook_delete`

Delete a notebook file and close its session if open. In **server** mode the file is removed via the Jupyter Contents API. In **local** mode it is removed from the filesystem. Idempotent — deleting an already-absent file still returns `ok: true`.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `notebook_path` | `string` | Yes | — | Path to the notebook to delete |

**Returns:** `ok`, `notebook_path`, `default_notebook`, `open_notebooks`

**Annotations:** `destructive_hint=true`, `idempotent_hint=true`

**Example prompt:** *"Delete the scratch notebook"*

---

## Code Execution

### `notebook_code_run`

Append a new code cell to the notebook, execute it, and return outputs.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `code` | `string` | Yes | — | Python code to execute |
| `timeout` | `float` | No | `120.0` | Execution timeout in seconds |

**Returns:** `ok`, `cell_index`, `execution_count`, `status`, `stdout`, `stderr`, `outputs`, `text_outputs`, `formatted_output`, `error_message`, `elapsed_seconds`

**Example prompt:** *"Run `print('Hello, world!')` in the notebook"*

---

### `notebook_code_run_existing`

Replace the source of an existing cell and re-execute it. Useful for updating a cell in-place instead of appending a new one.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `cell_index` | `int` | Yes | — | 0-based cell index to replace |
| `code` | `string` | Yes | — | New Python code for the cell |
| `timeout` | `float` | No | `30.0` | Execution timeout in seconds |

**Returns:** Same as `notebook_code_run`

> **Cell-type guard:** The target cell must be a code cell. If `cell_index` points to a markdown (or other non-code) cell, the tool raises a `TypeError` instead of silently overwriting it.

**Example prompt:** *"Update cell 2 to use a bar chart instead of a line chart"*

---

### `notebook_code_execute`

Execute code directly in the kernel **without** creating a notebook cell. Use this for background operations, introspection, or setup work that shouldn't appear in the notebook.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `code` | `string` | Yes | — | Python code to execute |
| `timeout` | `float` | No | `120.0` | Execution timeout in seconds |

**Returns:** `ok`, `status`, `stdout`, `stderr`, `outputs`, `text_outputs`, `formatted_output`, `error_message`, `elapsed_seconds`

**Example prompt:** *"Check if scikit-learn is importable without adding a cell"*

---

### `notebook_cells_run`

Execute multiple cells sequentially. Each cell can be code or markdown.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `cells` | `list[dict]` | Yes | — | List of `{"type": "code"|"markdown", "content": "..."}` |
| `timeout` | `float` | No | `120.0` | Per-cell execution timeout |

**Returns:** List of result dicts, one per cell

**Example prompt:** *"Add a markdown header, import pandas, and create a DataFrame — all in one go"*

---

## Notebook Document

### `notebook_markdown_add`

Add a markdown cell to the notebook. The cell is rendered in JupyterLab's UI. By default the cell is appended; pass `position` to insert it at a specific index.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `content` | `string` | Yes | — | Markdown content |
| `position` | `int` | No | `null` | 0-based index to insert the cell at. If omitted, the cell is appended to the end. |

**Returns:** `ok`, `cell_index`, `error_message`, `elapsed_seconds`

**Example prompt:** *"Add a title and description for this analysis"*

---

### `notebook_read`

Read the full notebook content — all cells, their sources, outputs, and metadata.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| *(none)* | — | — | — | — |

**Returns:** `ok`, `cell_count`, `cells` (list of `{index, cell_type, source, execution_count, outputs_count}`), `metadata`

**Example prompt:** *"Read the notebook and summarize what's been done so far"*

---

### `notebook_cell_delete`

Delete a cell from the notebook by its 0-based index.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `cell_index` | `int` | Yes | — | 0-based index of the cell to remove |

**Returns:** `ok`, `deleted_index`

**Example prompt:** *"Delete cell 3"*

---

## Cell Reads

### `notebook_cell_read`

Read a single cell by its 0-based index. Returns the full cell dict including type, source, outputs, and metadata — without fetching the entire notebook.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `cell_index` | `int` | Yes | — | 0-based cell index |

**Returns:** `ok`, `cell` (complete cell dict with `cell_type`, `source`, `outputs`, `metadata`)

**Example prompt:** *"Read cell 5"*

---

### `notebook_cell_source`

Return only the source text of a cell. This is a lightweight alternative to `notebook_cell_read` when you only need the code or markdown content.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `cell_index` | `int` | Yes | — | 0-based cell index |

**Returns:** `ok`, `cell_index`, `source`

**Example prompt:** *"Show me the source of cell 2"*

---

### `notebook_cell_count`

Return the number of cells currently in the notebook.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| *(none)* | — | — | — | — |

**Returns:** `ok`, `cell_count`

**Example prompt:** *"How many cells are in the notebook?"*

---

## Packages

### `notebook_packages_install`

Install Python packages in the kernel environment. Accepts pip-style version specifiers and skips packages that are already available.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `packages` | `list[str]` | Yes | — | Package specifiers (e.g. `["pandas>=2.0", "requests"]`) |

**Returns:** `ok`, `packages`, `report` (per-package details: `already_available`, `installed`, `failed`)

**Example prompt:** *"Install pandas and matplotlib"*

---

### `notebook_packages_check`

Check which packages are available in the kernel without installing anything.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `packages` | `list[str]` | Yes | — | Package names to check |

**Returns:** `ok`, `packages` (mapping of name → `true`/`false`)

**Example prompt:** *"Check if numpy and scipy are available"*

---

## Kernel Control

### `notebook_kernel_interrupt`

Send SIGINT to the kernel to stop a long-running or stuck computation. Does not restart the kernel.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| *(none)* | — | — | — | — |

**Returns:** `ok`

**Example prompt:** *"The last cell is stuck, interrupt it"*

---

### `notebook_kernel_info`

Get detailed kernel metadata including protocol version, implementation, language info, and the kernel banner.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| *(none)* | — | — | — | — |

**Returns:** `ok`, `protocol_version`, `implementation`, `implementation_version`, `language_info`, `banner`

**Example prompt:** *"What Python version is the kernel running?"*

---

### `notebook_session_info`

Get session info: kernel type, whether it's alive, connection details, and kernel name.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| *(none)* | — | — | — | — |

**Returns:** Session metadata dict

**Example prompt:** *"Is the kernel still alive?"*

---

### `notebook_kernel_history`

Retrieve recent execution history from the kernel. Returns the last *n* input entries (and optionally their outputs) from the kernel's history store.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `n` | `int` | No | `10` | Number of recent history entries to return |
| `output` | `bool` | No | `false` | Include outputs alongside inputs |
| `raw` | `bool` | No | `true` | Return raw (unprocessed) input |

**Returns:** `ok`, `entries` (list of history entries with session, line number, and source)

**Example prompt:** *"Show me the last 5 things that were executed"*

---

### `notebook_kernel_restart`

Restart the Jupyter kernel, clearing all state. Shuts down the running kernel and starts a fresh one. All variables, imports, and in-memory data are lost.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| *(none)* | — | — | — | — |

**Returns:** `ok`

**Example prompt:** *"Restart the kernel — I need a clean slate"*

---

## Introspection

### `notebook_inspect`

Inspect an object in the kernel at a given cursor position. Returns documentation, type info, and docstrings without executing any code.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `code` | `string` | Yes | — | Code containing the object to inspect |
| `cursor_pos` | `int` | Yes | — | Cursor position within the code |
| `detail_level` | `int` | No | `0` | `0` for brief, `1` for verbose |

**Returns:** Inspection result dict (status, data, metadata)

**Example prompt:** *"What methods does the `df` variable have?"*

---

### `notebook_complete`

Get tab-completion suggestions for code at a cursor position.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `code` | `string` | Yes | — | Partial code to complete |
| `cursor_pos` | `int` | Yes | — | Cursor position in the code |

**Returns:** Completion result dict (matches, cursor positions)

**Example prompt:** *"What attributes does `pd.DataFrame` have?"*

---

### `notebook_code_is_complete`

Check whether a code fragment is syntactically complete. The kernel parses the code and reports whether it is `'complete'`, `'incomplete'` (needs more input), or `'invalid'`. For incomplete code an indentation hint may be returned.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `code` | `string` | Yes | — | Code fragment to check |

**Returns:** `status` (`complete`, `incomplete`, `invalid`), `indent` (hint for incomplete code)

**Example prompt:** *"Is this code complete: `def foo():`?"*

---

## Variables

### `notebook_variables_list`

List all user-defined variables in the kernel's global scope. Excludes internal names starting with `_`.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| *(none)* | — | — | — | — |

**Returns:** `ok`, `variables` (list of variable metadata)

**Example prompt:** *"What variables are in the kernel right now?"*

---

### `notebook_variable_get`

Get the JSON-serializable value of a specific variable from the kernel.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | `string` | Yes | — | Variable name |

**Returns:** `ok`, `name`, `value`

**Example prompt:** *"What is the value of `total_revenue`?"*

---

### `notebook_variable_set`

Set a variable in the kernel's global scope, making it available for subsequent code cells.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | `string` | Yes | — | Variable name |
| `value` | `any` | Yes | — | JSON-serializable value |

**Returns:** `ok`, `name`

**Example prompt:** *"Set `threshold` to 0.95 in the kernel"*

---

## PostgreSQL Tools (optional)

These 8 tools are only available when you enable the `postgresql` tool set:

```bash
# Environment variable
export MCP_JUPYTER_ENABLE_TOOLS=postgresql

# CLI flag
mcp-jupyter-notebook --enable-tools postgresql
```

> All PostgreSQL tools accept an optional `notebook_path` parameter. Connection state lives in the notebook kernel as a Python variable (default: `pg_conn`).

### `postgresql_connect`

Create a PostgreSQL client/connection in the notebook kernel. Installs `psycopg[binary]` and (optionally) `agent-data-toolkit[postgresql]`, then connects using a DSN or individual parameters.

Connection details resolve in order:

1. Explicit `dsn` parameter
2. MCP server env (when `use_mcp_env_dsn=True`): `PG_DSN` → `POSTGRES_DSN` → `DATABASE_URL`
3. Kernel env vars: `PG_DSN` → `POSTGRES_DSN` → `DATABASE_URL`
4. Individual libpq env vars: `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGSSLMODE`
5. Explicit `host`/`port`/`database`/`user`/`password` parameters

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `notebook_path` | `string` | No | — | Target notebook |
| `connection_name` | `string` | No | `pg_conn` | Kernel variable name for the connection |
| `dsn` | `string` | No | — | Full PostgreSQL DSN / connection URI |
| `prefer_agent_data_toolkit` | `bool` | No | `true` | Wrap in `PostgresClient` if available |
| `use_mcp_env_dsn` | `bool` | No | `false` | Forward DSN from the MCP server process env |
| `require_ssl` | `bool` | No | `false` | Enforce a minimum SSL mode |
| `default_sslmode` | `string` | No | `require` | SSL mode when not specified in DSN |
| `host` | `string` | No | — | PostgreSQL host |
| `port` | `int` | No | `5432` | PostgreSQL port |
| `database` | `string` | No | — | Database name |
| `user` | `string` | No | — | User name |
| `password` | `string` | No | — | Password |
| `sslmode` | `string` | No | — | Explicit SSL mode |
| `autocommit` | `bool` | No | `true` | Enable autocommit on the connection |
| `connect_timeout_seconds` | `int` | No | `10` | Connection timeout |
| `test_query` | `string` | No | `SELECT 1` | Query to run after connecting (`null` to skip) |

**Returns:** `ok`, `connection_name`, `parsed` (connection details), `cell_index`

**Example prompt:** *"Connect to my Postgres database"*

### `postgresql_test_connection`

Validate that a kernel connection exists and can execute a query. If `auto_connect_from_env=True` and the connection variable is missing, it attempts to create one from kernel env vars.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `notebook_path` | `string` | No | — | Target notebook |
| `connection_name` | `string` | No | `pg_conn` | Kernel variable name |
| `test_query` | `string` | No | `SELECT 1` | Query to validate the connection |
| `auto_connect_from_env` | `bool` | No | `true` | Auto-create connection from env if missing |

**Returns:** `ok`, `connection_name`, `result`, `error`

**Example prompt:** *"Test that my Postgres connection is working"*

### `postgresql_query_to_df`

Execute a SQL query and create a Pandas DataFrame in the notebook. Runs the query **hidden** (no plumbing visible), then inserts a **clean, readable cell** showing the `PostgresClient` API.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `raw_sql` | `string` | Yes | — | SQL query to execute |
| `notebook_path` | `string` | No | — | Target notebook |
| `connection_name` | `string` | No | `pg_conn` | Kernel variable name for the connection |
| `df_name` | `string` | No | auto-generated | DataFrame variable name |
| `limit` | `int` | No | `50` | Row limit (`null` for no limit) |
| `preview_rows` | `int` | No | `5` | Rows to display via `head()` |

**Returns:** `ok`, `df_name`, `cell_index`, `schema`, `row_count`, `col_count`, `limit`, `limit_applied`, `sample`, `error`

The visible notebook cell looks like:

```python
pg = globals().get("pg_conn")
_sql = "SELECT * FROM public.users"

df_abc123 = pg.query_df(sql=_sql, limit=50)
df_abc123.head(5)
```

**Example prompt:** *"Query all users from the demo_users table into a DataFrame"*

### `postgresql_schema_list_tables`

List tables and views (and optionally materialized views) from `information_schema.tables`.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `notebook_path` | `string` | No | — | Target notebook |
| `connection_name` | `string` | No | `pg_conn` | Kernel variable name |
| `schema_name` | `string` | No | — | Filter to a specific schema |
| `include_matviews` | `bool` | No | `false` | Include materialized views |

**Returns:** `ok`, `tables` (list of `{table_schema, table_name, table_type}`), `error`

**Example prompt:** *"List all tables in the public schema"*

### `postgresql_schema_list_columns`

List column metadata for a specific table.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `schema_name` | `string` | Yes | — | Schema name |
| `table` | `string` | Yes | — | Table name |
| `notebook_path` | `string` | No | — | Target notebook |
| `connection_name` | `string` | No | `pg_conn` | Kernel variable name |

**Returns:** `ok`, `columns` (list of `{column_name, data_type, is_nullable, column_default, ordinal_position}`), `error`

**Example prompt:** *"Show me the columns for public.demo_users"*

### `postgresql_schema_tree`

Return a compact schema → tables mapping, filtering out system schemas (`pg_catalog`, `information_schema`, etc.).

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `notebook_path` | `string` | No | — | Target notebook |
| `connection_name` | `string` | No | `pg_conn` | Kernel variable name |
| `limit_per_schema` | `int` | No | `100` | Max tables per schema |

**Returns:** `ok`, `schemas` (list of `{schema, tables[]}`), `error`

**Example prompt:** *"Show me the database schema tree"*

### `postgresql_reset`

Forcibly close and re-create the connection variable from kernel environment variables. Useful when Postgres has been restarted and the connection is stale.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `notebook_path` | `string` | No | — | Target notebook |
| `connection_name` | `string` | No | `pg_conn` | Kernel variable name |

**Returns:** `ok`, `connection_name`, `created`, `error`

**Example prompt:** *"Reset the Postgres connection"*

### `postgresql_close`

Close a PostgreSQL connection/client stored in a kernel variable. Supports both raw `psycopg.Connection` and `PostgresClient`.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `notebook_path` | `string` | No | — | Target notebook |
| `connection_name` | `string` | No | `pg_conn` | Kernel variable name |

**Returns:** `ok`, `closed`, `connection_name`

**Example prompt:** *"Close the Postgres connection"*
