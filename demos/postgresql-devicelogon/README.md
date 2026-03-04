# DeviceLogonEvents — PostgreSQL + MCP Jupyter Demo

Explore Microsoft Defender for Endpoint **DeviceLogonEvents** data using the
MCP Jupyter Notebook server with PostgreSQL tools.

## Data source

The dataset used in this demo comes from the YouTube video
**[KQL For Beginners | Kusto Query Language (Cybersecurity 2026)](https://youtu.be/L1Av7vlrhkU?si=qsSh5usoZn_uGd3O)**
by **[Josh Madakor](https://www.youtube.com/@JoshMadakor)** — a practical,
end-to-end walkthrough of KQL log analysis using Microsoft Sentinel and
Microsoft Defender for real security operations.

### Download the data

1. Open the Google Sheets export:
   **[DeviceLogonEvents — Google Sheets](https://docs.google.com/spreadsheets/d/1ekMwzyWro7TaqxGTMqc8l9o83bAwokzYh7Bd7GhIIMI/edit?usp=sharing)**
2. **File → Download → Comma-separated values (.csv)**
3. Rename the downloaded file and place it in this demo's `data/` folder:

   ```bash
   mv ~/Downloads/<downloaded-file>.csv demos/postgresql-devicelogon/data/DeviceLogonEvents.csv
   ```

   The ingestion script expects the file at exactly
   `data/DeviceLogonEvents.csv`.

## What's included

| File / Folder | Purpose |
|---|---|
| `data/DeviceLogonEvents.csv` | ~4,700 logon events (Defender for Endpoint export) |
| `docker-compose.yml` | PostgreSQL 16 + JupyterLab (with `agent-data-toolkit`) |
| `docker-entrypoint-initdb.d/01_init.sql` | Creates the `device_logon_events` table + indexes |
| `docker-entrypoint-initdb.d/02_load_data.sql` | `COPY FROM` — auto-loads the CSV into PostgreSQL at first boot |
| `scripts/ingest.py` | *(optional)* Python alternative to re-ingest via `PostgresClient` |
| `jupyter/` | Dockerfile + requirements for the Jupyter image |
| `notebooks/` | Working directory mounted into JupyterLab |

## Quick start

The CSV is automatically loaded into PostgreSQL on first `docker compose up` —
no manual ingestion step needed.

```bash
# 1. Copy env and adjust if needed
cp .env.example .env

# 2. Start PostgreSQL + Jupyter (first boot creates table + loads CSV)
docker compose up --build

# 3. Open JupyterLab (in another terminal, or wait for the "ready" log)
open http://localhost:8888?token=mcp-dev-token

# 4. Stop everything (Ctrl+C in the terminal running compose)
```

> **How it works:** The `data/` folder is bind-mounted into the Postgres
> container at `/data/`. On first init, `01_init.sql` creates the table and
> indexes, then `02_load_data.sql` uses PostgreSQL's `COPY FROM` to bulk-load
> the CSV through a staging table with proper type casting.

### Reset to a fresh database

PostgreSQL init scripts only run when the data volume is empty. To start
completely fresh (re-create the table and re-load the CSV):

```bash
# Stop containers and remove the named pgdata volume
docker compose down -v

# Start again — initdb re-runs from scratch
docker compose up --build
```

### Alternative: Python ingestion script

If you need to re-ingest (e.g., after truncating the table or updating the
CSV), you can use the Python script instead:

```bash
pip install agent-data-toolkit[postgresql] pandas python-dotenv
python scripts/ingest.py
```

It reads `.env` for connection settings and uses `PostgresClient.copy_df_in()`
for bulk loading.

## Using with MCP Jupyter Notebook server

Point the MCP server at this demo's Jupyter + PostgreSQL by setting these
env vars in `.vscode/mcp.json`:

```jsonc
{
  "MCP_JUPYTER_SESSION_MODE": "server",
  "MCP_JUPYTER_NOTEBOOK_PATH": "agent_notebook_demo.ipynb",
  "MCP_JUPYTER_BASE_URL": "http://localhost:8888",
  "MCP_JUPYTER_TOKEN": "mcp-dev-token",
  "MCP_JUPYTER_ENABLE_TOOLS": "postgresql"
}
```

The PostgreSQL tools (`postgresql_connect`, `postgresql_query_to_df`,
`postgresql_schema_tree`, etc.) will connect using the `PG_DSN` env var
that the Jupyter container inherits from `docker-compose.yml`.

## Demo scenario

```
<Rules>

- Use the MCP Jupyter Notebook server tools for all notebook and PostgreSQL
  interaction. Start by connecting to PostgreSQL, then use schema exploration
  tools (`postgresql_schema_tree`, `postgresql_schema_list_tables`,
  `postgresql_schema_list_columns`) to understand table structures before
  writing any queries. Sample data first (`postgresql_query_to_df` with a
  broad SELECT) to see what you are working with, then iterate with
  DataFrames — progressively refining your analysis in code cells.
- Before importing a library, verify whether it is already installed in the
  kernel environment. If it is not installed, install it first (e.g., using
  `notebook_packages_install` or `!pip install <package>`) before importing.
- Be intentional with SQL `LIMIT` clauses — sometimes you need the full
  result set to spot patterns; other times a targeted slice is enough.
  Remember you are doing iterative analysis: pull data into DataFrames, then
  use pandas for exploration, filtering, aggregation, and visualization.
  Expand or restrict as each step of the analysis demands.
- Start with broad exploratory queries to understand the shape of the data
  (row counts, distinct values, time ranges), then progressively narrow focus.
- Prefer matplotlib for visualizations (bar charts, timelines, heatmaps) to
  structure your analysis and surface patterns or outliers — it produces
  static PNG images that render reliably in all notebook environments. Other
  libraries such as plotly are also available if interactivity is needed.
- Use markdown cells to document your reasoning — explain what you are looking
  for, what you found, and what it means before moving to the next step.
- SQL is for retrieval; pandas is for analysis. Avoid overly restrictive WHERE
  clauses that may hide relevant context — retrieve generously, then slice in
  the DataFrame.

</Rules>

Remote Desktop Protocol connections leave network traces that can identify the
source of unauthorized access. Determining the origin helps with threat actor
attribution and stopping ongoing attacks.

The analysis should focus on authentication activity recorded in the
`device_logon_events` table. Explore the dataset to understand external remote
access patterns — which endpoints received RDP connections, from which source
IPs, and whether any of that activity looks anomalous. Look for outliers,
unusual access times, repeated failed logons, or public IPs connecting to
internal hosts.

> **Objective:** Investigate Remote Desktop Protocol (RDP) activity across the
> environment. Identify which endpoints were accessed externally, surface any
> suspicious patterns, and determine the source IPs involved.
>
> *What does the data tell you?*
```

## Table schema

The `device_logon_events` table has ~80 columns covering:

- **Timestamps**: `time_generated`, `timestamp`
- **Device/Tenant**: `device_name`, `tenant_id`, `machine_group`
- **Account**: `account_name`, `account_domain`, `account_sid`
- **Logon details**: `action_type`, `logon_type`, `failure_reason`, `protocol`
- **Remote endpoint**: `remote_ip`, `remote_port`, `remote_device_name`
- **Initiating process**: full chain including command line, hashes, parent info
- **Additional**: `additional_fields` (JSONB)

## Example queries

```sql
-- Failed logons by remote IP
SELECT remote_ip, COUNT(*) AS failures
FROM device_logon_events
WHERE action_type = 'LogonFailed'
GROUP BY remote_ip
ORDER BY failures DESC
LIMIT 10;

-- Logon types distribution
SELECT logon_type, action_type, COUNT(*) AS cnt
FROM device_logon_events
GROUP BY logon_type, action_type
ORDER BY cnt DESC;

-- Suspicious: failed network logons from public IPs
SELECT account_name, remote_ip, failure_reason, time_generated
FROM device_logon_events
WHERE action_type = 'LogonFailed'
  AND logon_type = 'Network'
  AND remote_ip_type = 'Public'
ORDER BY time_generated DESC
LIMIT 20;
```
