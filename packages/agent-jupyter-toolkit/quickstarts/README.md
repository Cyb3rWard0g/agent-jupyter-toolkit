# Quickstarts

This folder contains small, runnable scripts that exercise common workflows in
agent-jupyter-toolkit. Each scenario is designed to be copy-and-run with
minimal setup.

## Prerequisites

- Install dev/runtime dependencies: `uv pip install -e ".[dev]"`
- Use uv to run scripts/tests in the project environment: `uv run python ...`

If you want server-backed examples, start a Jupyter Server with a known token:

```bash
uv pip install jupyter-server ipykernel jupyter-collaboration
TOKEN="$(uv run python -c "import uuid; print(uuid.uuid4())")"
echo "TOKEN=$TOKEN"
uv run jupyter server --port 8888 --ServerApp.port_retries 0 --IdentityProvider.token "$TOKEN"
```

## Scenario 1: Local Kernel Execution

Script: `quickstarts/01_local_kernel.py`

What it does
- Starts a local kernel using `SessionConfig(mode="local")`
- Executes a small code snippet
- Prints the normalized `ExecutionResult`

Run it
```bash
uv run python quickstarts/01_local_kernel.py
```

## Scenario 2: Remote Kernel Execution (Standalone Kernel)

Script: `quickstarts/02_server_kernel.py`

What it does
- Connects to a remote Jupyter Server
- Creates a standalone kernel (not tied to a notebook)
- Executes code and prints outputs

Environment variables
- `JUPYTER_BASE_URL` (required): e.g., `http://localhost:8888`
- `JUPYTER_TOKEN` (optional if your server does not require a token)
- `JUPYTER_KERNEL_NAME` (optional, default `python3`)

Run it
```bash
export JUPYTER_BASE_URL="http://localhost:8888"
export JUPYTER_TOKEN="MY_TOKEN"
uv run python quickstarts/02_server_kernel.py
```

## Scenario 3: Remote Notebook Session (Kernel + Notebook)

Script: `quickstarts/03_server_notebook_session.py`

What it does
- Opens or creates a notebook on a remote server
- Binds the kernel to that notebook session
- Appends a cell, executes it, and persists outputs

Environment variables
- `JUPYTER_BASE_URL` (required)
- `JUPYTER_TOKEN` (optional)
- `JUPYTER_NOTEBOOK_PATH` (required): e.g., `quickstarts/demo.ipynb`
- `JUPYTER_HEADERS_JSON` (optional): JSON for extra headers/cookies
- `JUPYTER_PREFER_COLLAB` (optional): `1` to prefer Yjs collab transport

Run it
```bash
export JUPYTER_BASE_URL="http://localhost:8888"
export JUPYTER_TOKEN="MY_TOKEN"
export JUPYTER_NOTEBOOK_PATH="quickstarts/demo.ipynb"
uv run python quickstarts/03_server_notebook_session.py
```

## Scenario 4: Local Notebook Session (Kernel + Local .ipynb)

Script: `quickstarts/04_local_notebook_session.py`

What it does
- Creates a local notebook file if missing
- Appends a markdown cell and a code cell
- Executes the code cell and saves outputs

Environment variables
- `LOCAL_NOTEBOOK_PATH` (optional, default `quickstarts/output/local_demo.ipynb`)

Run it
```bash
uv run python quickstarts/04_local_notebook_session.py
```

## Scenario 5: Local Autosave (Debounced Writes)

Script: `quickstarts/05_local_autosave.py`

What it does
- Creates a local notebook with a debounced autosave delay
- Performs multiple edits in quick succession
- Flushes writes after the debounce window
- Produces input cells only (no outputs) because it does not execute a kernel

Run it
```bash
uv run python quickstarts/05_local_autosave.py
```

## Scenario 6: In-Memory Notebook Buffer

Script: `quickstarts/06_notebook_buffer.py`

What it does
- Loads a notebook into a memory buffer
- Applies multiple edits without writing each step
- Commits the final notebook in one write

Run it
```bash
uv run python quickstarts/06_notebook_buffer.py
```

## Scenario 7: Collaboration Awareness (Presence Metadata)

Script: `quickstarts/07_collab_awareness.py`

What it does
- Connects to a collaborative notebook session
- Sets local awareness state (presence metadata)
- Prints the current awareness state map

Notes
- Requires a Jupyter Server with collaboration enabled (`/api/collaboration/*`).
- If you see a 404, your server likely does not have collab features enabled.

Environment variables
- `JUPYTER_BASE_URL` (required)
- `JUPYTER_NOTEBOOK_PATH` (required)
- `JUPYTER_TOKEN` (optional)
- `JUPYTER_USERNAME` (optional, default `agent`)

Run it
```bash
export JUPYTER_BASE_URL="http://localhost:8888"
export JUPYTER_NOTEBOOK_PATH="quickstarts/output/awareness_demo.ipynb"
export JUPYTER_TOKEN="YOUR_TOKEN"
uv run python quickstarts/07_collab_awareness.py
```
