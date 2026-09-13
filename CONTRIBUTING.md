# Contributing

Thank you for your interest in contributing to the Agent Jupyter Workspace!

This is a **uv workspace** monorepo with two publishable packages:

- `agent-jupyter-toolkit` — the domain library
- `mcp-jupyter-notebook` — the MCP server

---

## Development Setup

```sh
# Prerequisites: uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install (editable, both packages + dev deps)
git clone https://github.com/Cyb3rWard0g/agent-jupyter-toolkit.git
cd agent-jupyter-toolkit
uv sync --locked --all-packages --all-extras
```

This creates a `.venv` with both packages installed in editable mode, including
the local kernel, DataFrame, batch execution, and server integration dependencies.
Use the checked-in lockfile for normal development; run `uv lock` intentionally
when changing dependencies and include the resulting `uv.lock` in the commit.

## Quality Checks

Run all quality checks before committing. These same checks run in CI on every
push and PR to `main`.

### Lint

```sh
# Check for lint errors
uv run ruff check packages/

# Auto-fix lint errors (import sorting, etc.)
uv run ruff check packages/ --fix
```

### Format

```sh
# Check formatting (dry run)
uv run ruff format --check packages/

# Auto-format
uv run ruff format packages/
```

### Tests

```sh
# Run all tests across both packages
uv run --locked --all-packages --all-extras pytest

# Run tests for a specific package
uv run --locked --all-packages --all-extras pytest packages/agent-jupyter-toolkit/tests -q
uv run --locked --all-packages --all-extras pytest packages/mcp-jupyter-notebook/tests -q
```

### Full Pre-Push Check

Run everything in one go before pushing:

```sh
uv run ruff check packages/ && \
uv run ruff format --check packages/ && \
uv run --locked --all-packages --all-extras pytest --tb=short -q
```

`uv run tox` also runs both package suites in separate environments, plus lint
and format checks. Server tests skip unless `JAT_SERVER_URL` and
`JAT_SERVER_TOKEN` point to a test Jupyter Server. Collaboration tests use
`JAT_COLLAB_URL` and `JAT_COLLAB_TOKEN` and require its collaboration extension.
Use an isolated server root because these tests create notebooks and kernels.

The MCP tox environment installs the toolkit as a local package dependency.
After changing toolkit code, use `uv run tox -r -e mcp` to rebuild that environment
so it tests the current toolkit instead of a previously installed build.

After the checks pass, review `git diff` and `git diff --check`, stage the files
for one coherent change, and inspect `git diff --cached` before committing.
Use a concise commit subject describing the behavior changed; explain the reason
and validation in the body for substantial changes. Local commits do not publish
packages or create releases.

---

## Project Structure

```
packages/
├── agent-jupyter-toolkit/    # Domain library (kernel, notebook, utils)
│   ├── pyproject.toml
│   ├── src/agent_jupyter_toolkit/
│   ├── tests/
│   └── quickstarts/
└── mcp-jupyter-notebook/     # MCP server (tools for AI agents)
    ├── pyproject.toml
    ├── src/mcp_jupyter_notebook/
    ├── tests/
    └── quickstarts/
```

See [docs/architecture.md](docs/architecture.md) for the full design.

---

## CI / CD

Two GitHub Actions workflows live in `.github/workflows/`:

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | Push / PR to `main` | Lint/format, Python 3.11–3.13 tests, real server/collaboration, dependency resolution, extras, and local platform checks |
| `release.yml` | Push a `v*` tag | Build → test → publish to PyPI → GitHub Release → Docker image |

**Pushing to `main` never triggers a release.** Only pushing a version tag does.

---

## Release Process

Both packages share a **single version** derived from **git tags** via `hatch-vcs`.
There is no hard-coded version anywhere — one tag drives both packages.

> **Versioning note:** Both packages always release with the same version number.
> This is intentional — `mcp-jupyter-notebook` depends on `agent-jupyter-toolkit`
> and they are tightly coupled.

### Automated Release (recommended)

#### 1. Run quality checks locally

```sh
uv run ruff check packages/
uv run ruff format --check packages/
uv run --locked --all-packages --all-extras pytest --tb=short -q
```

#### 2. Determine the next version

```sh
# Show the latest tag
git tag -l 'v*' | sort -V | tail -1

# Bump accordingly:
#   patch:  vX.Y.Z → vX.Y.(Z+1)
#   minor:  vX.Y.Z → vX.(Y+1).0
#   major:  vX.Y.Z → v(X+1).0.0
```

When the MCP server uses new toolkit APIs, set its toolkit dependency minimum
in `packages/mcp-jupyter-notebook/pyproject.toml` to the release that first
provides those APIs, including the `dataframe` extra. Do this when selecting
the release version, then refresh the lockfile and rerun checks. Workspace
installs use the local toolkit and do not test compatibility with older PyPI
releases.

#### 3. Tag and push

```sh
git tag -a vX.Y.Z -m "Release X.Y.Z"
git push origin vX.Y.Z
```

This triggers the `release.yml` workflow which:
1. Builds both packages (`python -m build`)
2. Runs the full test matrix (Python 3.11–3.13)
3. Installs the built wheel pair and verifies their versions and workspace APIs
4. Publishes `agent-jupyter-toolkit` to PyPI (via OIDC trusted publishing)
5. Waits until that toolkit version is installable, then publishes `mcp-jupyter-notebook`
6. Creates a GitHub Release with auto-generated notes and attached artifacts
7. Builds and pushes a Docker image to GHCR (`ghcr.io/cyb3rward0g/mcp-jupyter-notebook`)

### Manual Release (fallback)

If you need to build and upload manually:

```sh
git checkout vX.Y.Z
rm -rf packages/*/dist packages/*/build

# Build
cd packages/agent-jupyter-toolkit && uv run python -m build && cd ../..
cd packages/mcp-jupyter-notebook && uv run python -m build && cd ../..

# Check
uv run python -m twine check packages/agent-jupyter-toolkit/dist/*
uv run python -m twine check packages/mcp-jupyter-notebook/dist/*

# Upload (toolkit first — MCP server depends on it)
uv run python -m twine upload packages/agent-jupyter-toolkit/dist/*
uv run python -m twine upload packages/mcp-jupyter-notebook/dist/*

git switch -
```

---

## PyPI Trusted Publishing Setup

The release workflow uses OIDC trusted publishing (no API tokens needed).
Configure this **once per package** on PyPI and **once** on GitHub:

### GitHub Environment (one-time)

1. Go to **https://github.com/Cyb3rWard0g/agent-jupyter-toolkit/settings/environments**
2. Click **"New environment"** → name it **`pypi`** → click **"Configure environment"**
3. Done — the environment just needs to exist with that name

### PyPI Publishers (one-time, per package)

For each package, go to its publishing settings on PyPI and add a trusted publisher:

| Field | Value |
|---|---|
| **Owner** | `Cyb3rWard0g` |
| **Repository** | `agent-jupyter-toolkit` |
| **Workflow** | `release.yml` |
| **Environment** | `pypi` |

Configure at:
- https://pypi.org/manage/project/agent-jupyter-toolkit/settings/publishing/
- https://pypi.org/manage/project/mcp-jupyter-notebook/settings/publishing/

---

## Notes

- **Shared versioning:** Both packages always share the same version, derived
  from a single git tag via `hatch-vcs`. There is no hard-coded version in
  the source. You must tag before building.
- **`uv run`** automatically resolves the project environment from `pyproject.toml`;
  there is no need to manually create or activate a virtualenv.
- Build/upload dependencies (`build`, `twine`) are listed under
  `[dependency-groups] dev` in the root `pyproject.toml`.

## Removing Tags

If you need to delete tags and start fresh:

```sh
# Delete ALL remote tags
git ls-remote --tags origin \
  | awk '{print $2}' \
  | sed 's|refs/tags/||' \
  | grep -v '\^{}' \
  | xargs -I{} git push origin :refs/tags/{}

# Delete ALL local tags
git tag -l | xargs git tag -d

# Delete a single tag (local + remote)
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z
```
