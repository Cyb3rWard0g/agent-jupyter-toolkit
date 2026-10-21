# Azure Monitor Logs / Sentinel

Enable with `--enable-tools azure-monitor` or
`MCP_JUPYTER_ENABLE_TOOLS=azure-monitor`. The MCP server manages execution; Azure
requests originate from the notebook kernel, using `agent-data-toolkit`'s optional
Azure Monitor connector. Querying Logs does not provide Sentinel rule/incident
management or establish security-event semantics.

## Kernel setup

Install `agent-data-toolkit[azure-monitor]` into the kernel image/environment
before starting. Tools do not install packages or accept credentials from model
arguments. Configure `AZURE_LOGS_WORKSPACE_ID` and one explicit authentication mode:

- `AZURE_LOGS_AUTH=managed-identity`: Azure-hosted runtime identity; optional
  `AZURE_CLIENT_ID` selects a user-assigned identity.
- `AZURE_LOGS_AUTH=azure-cli`: explicit local development identity available to
  the kernel process. Authenticate that CLI separately.

Workspace settings and credentials are resolved in the kernel environment, even
when the MCP server is on another machine. The Jupyter server's login token and
Azure tokens have separate purposes. SDK credential/client objects are reused
across queries and handle token renewal. Credentials are never embedded in cells.

Any code in that kernel can use its identity. Optional tools, connection variable
names and notebook paths are not a source authorization boundary. Enforce source
access using Azure permissions and the enclosing runtime/application policy.

## Tools

`azure_monitor_connect(notebook_path?, connection_name="logs", timeout=30)`
creates or reuses a kernel-side client from trusted environment settings. It
does not contact the source or verify workspace permission. It leaves a different
object already occupying the connection name unchanged and reports the error.

`azure_monitor_query_to_df(query, start_time, end_time, notebook_path?, ...)`
creates one visible cell containing the actual query, explicit absolute time
window and DataFrame conversion. It can initialize a missing client from the
same environment. Both timestamps must include a timezone.

Optional query parameters:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `connection_name` | `logs` | Reusable kernel client variable |
| `df_name` | Unique name | DataFrame variable for subsequent cells |
| `table` | `0` | Index of the returned table to analyze |
| `allow_partial` | `false` | Explicitly allow DataFrame creation from incomplete data |
| `max_rows` | `100000` | Total client-retained row limit across tables; maximum 1000000 |
| `preview_rows` | `5` | Preview size, 0–20; oversized serialized previews are omitted |
| `server_timeout` | `120` | Azure server timeout, 1–600 seconds |
| `timeout` | `150` | Notebook execution timeout, greater than 0 and at most 600 seconds |

`max_rows` acts after Azure returns its response. It does not bound server scan
volume, incoming network bytes or the native response retained by the connector.
Use bounded KQL and the service's own limits as well. A successful response does
not establish complete source ingestion or investigation coverage.

`azure_monitor_close(notebook_path?, connection_name="logs", timeout=30)`
closes/removes the client and keeps existing datasets and notebook cells.

## Results and concurrency

The query response retains the existing notebook execution fields: cell ID/index,
request/source identity, kernel generation, outcome, timeout, output truncation,
and persistence/callback status. `result_name` identifies a unique kernel record
holding the structured connector result and, when created, its exact DataFrame.
`source` contains source status, original table schemas/counts, window, request ID,
query/response hashes and explicit client truncation. Native source bytes remain
available through that record's `result.raw_response` for an application-owned
evidence export; they are not sent to the model as preview text.

The source request runs once. A hidden follow-up execution reads metadata from
the retained record without source access. `metadata_extracted` and
`metadata_execution` describe that separate step. Failed query/conversion does
not claim a previously existing DataFrame as a new result. Partial source data
remain inspectable even when default DataFrame conversion rejects them.

An unknown query outcome returns immediately without metadata execution or an
automatic query rerun. A saved cell does not prove its source request completed;
an executed cell does not prove its outputs were saved. Check both execution and
persistence fields. Connector/SDK transport retries are independent of tool retries.

Connection/query/close tool workflows are serialized per notebook session. Query
metadata refers to the unique retained result, not a user-chosen variable that a
later query may reassign. Generic code tools, browser users and other clients do
not share this optional-tool lock. Coordinate writers; this is not transactional
isolation against arbitrary notebook code. Pass explicit notebook paths when
multiple callers share a workspace, whose default notebook is shared.

Kernel shutdown loses client objects and live variables. Reopen/reconstruct them
from trusted runtime configuration and saved data; reopening an `.ipynb` does not
restore arbitrary Python memory. Rerunning query cells queries current source data.

## Qualification

The owning unit suite covers invalid input, registration and unknown outcomes.
`tests/integration/test_azure_monitor_kernel.py` uses an actual local kernel and
the actual Azure SDK with an in-memory transport. It verifies visible cells,
one source request, retained DataFrames, partial/empty/failure responses and
concurrent-call result identity. It requires the kernel-side Azure extra and no
Azure credentials. This controlled check does not replace live source identity,
workspace/table access or an enclosing application's evidence acceptance.
