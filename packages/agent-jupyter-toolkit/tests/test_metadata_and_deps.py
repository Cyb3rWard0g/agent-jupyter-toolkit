"""
Tests for notebook metadata read/write and dependency tracking.

These tests verify:
  - LocalFileDocumentTransport.get_metadata() / update_metadata()
  - NotebookSession.install_packages() / uninstall_packages() / get_tracked_dependencies()
"""

from unittest.mock import AsyncMock, MagicMock, patch

import nbformat
import pytest

from agent_jupyter_toolkit.notebook import NotebookSession, make_document_transport

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Transport-level metadata tests (no kernel needed)
# ---------------------------------------------------------------------------


async def test_get_metadata_returns_notebook_level_metadata(tmp_path):
    """get_metadata() should return the notebook's top-level metadata."""
    nb_path = tmp_path / "meta.ipynb"
    nb = nbformat.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3"}
    nb.metadata["custom_key"] = "hello"
    nbformat.write(nb, nb_path)

    transport = make_document_transport(
        mode="local",
        local_path=str(nb_path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
    )
    await transport.start()

    meta = await transport.get_metadata()
    assert meta["custom_key"] == "hello"
    assert meta["kernelspec"]["name"] == "python3"


async def test_update_metadata_merges_keys(tmp_path):
    """update_metadata() should merge new keys and preserve existing ones."""
    nb_path = tmp_path / "meta_merge.ipynb"
    nb = nbformat.v4.new_notebook()
    nb.metadata["existing"] = "keep_me"
    nbformat.write(nb, nb_path)

    transport = make_document_transport(
        mode="local",
        local_path=str(nb_path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
    )
    await transport.start()

    await transport.update_metadata({"new_key": "new_value", "another": 42})

    meta = await transport.get_metadata()
    assert meta["existing"] == "keep_me"
    assert meta["new_key"] == "new_value"
    assert meta["another"] == 42

    # Verify it persisted to disk
    on_disk = nbformat.read(nb_path, as_version=4)
    assert on_disk.metadata["new_key"] == "new_value"
    assert on_disk.metadata["existing"] == "keep_me"


async def test_update_metadata_overwrites_existing_key(tmp_path):
    """update_metadata() should overwrite an existing key."""
    nb_path = tmp_path / "meta_overwrite.ipynb"
    nb = nbformat.v4.new_notebook()
    nb.metadata["version_info"] = "old"
    nbformat.write(nb, nb_path)

    transport = make_document_transport(
        mode="local",
        local_path=str(nb_path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
    )
    await transport.start()

    await transport.update_metadata({"version_info": "new"})

    meta = await transport.get_metadata()
    assert meta["version_info"] == "new"


async def test_update_metadata_rejects_non_dict(tmp_path):
    """update_metadata() should raise TypeError on non-dict input."""
    nb_path = tmp_path / "meta_bad.ipynb"
    nb = nbformat.v4.new_notebook()
    nbformat.write(nb, nb_path)

    transport = make_document_transport(
        mode="local",
        local_path=str(nb_path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
    )
    await transport.start()

    with pytest.raises(TypeError, match="must be a dict"):
        await transport.update_metadata("not_a_dict")


async def test_update_metadata_fires_change_callback(tmp_path):
    """update_metadata() should fire on_change callbacks."""
    nb_path = tmp_path / "meta_cb.ipynb"
    nb = nbformat.v4.new_notebook()
    nbformat.write(nb, nb_path)

    transport = make_document_transport(
        mode="local",
        local_path=str(nb_path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
    )
    await transport.start()

    events = []
    transport.on_change(events.append)

    await transport.update_metadata({"foo": "bar"})
    assert len(events) == 1
    assert events[0]["op"] == "metadata-updated"
    assert "foo" in events[0]["keys"]


# ---------------------------------------------------------------------------
# Session-level dependency tracking tests (mocked kernel)
# ---------------------------------------------------------------------------


def _make_mock_kernel():
    """Create a mock kernel session with async methods."""
    kernel = MagicMock()
    kernel.start = AsyncMock()
    kernel.shutdown = AsyncMock()
    kernel.is_alive = AsyncMock(return_value=True)
    kernel.execute = AsyncMock()
    return kernel


async def test_install_packages_tracks_in_metadata(tmp_path):
    """install_packages() should install and write to notebook metadata."""
    nb_path = tmp_path / "deps.ipynb"
    nb = nbformat.v4.new_notebook()
    nbformat.write(nb, nb_path)

    kernel = _make_mock_kernel()
    transport = make_document_transport(
        mode="local",
        local_path=str(nb_path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
    )
    session = NotebookSession(kernel=kernel, doc=transport)
    await session.start()

    mock_install_report = {
        "success": True,
        "report": {
            "pandas": {
                "pip": "pandas",
                "already": False,
                "installed": True,
                "success": True,
                "error": None,
            },
            "numpy": {
                "pip": "numpy",
                "already": True,
                "installed": False,
                "success": True,
                "error": None,
            },
        },
    }
    mock_versions = {"pandas": "2.1.0", "numpy": "1.26.0"}

    with (
        patch(
            "agent_jupyter_toolkit.utils.packages.ensure_packages_with_report",
            new=AsyncMock(return_value=mock_install_report),
        ),
        patch(
            "agent_jupyter_toolkit.utils.packages.get_package_versions",
            new=AsyncMock(return_value=mock_versions),
        ),
    ):
        result = await session.install_packages(["pandas", "numpy"])

    assert result["success"] is True
    assert set(result["tracked"]) == {"pandas", "numpy"}

    # Verify metadata was persisted
    deps = await session.get_tracked_dependencies()
    assert "pandas" in deps
    assert deps["pandas"]["version"] == "2.1.0"
    assert "numpy" in deps
    assert deps["numpy"]["version"] == "1.26.0"
    assert "installed_at" in deps["pandas"]

    # Verify on disk
    on_disk = nbformat.read(nb_path, as_version=4)
    assert "agent_dependencies" in on_disk.metadata
    assert on_disk.metadata["agent_dependencies"]["pandas"]["version"] == "2.1.0"


async def test_install_packages_no_track_skips_metadata(tmp_path):
    """install_packages(track=False) should install but NOT write metadata."""
    nb_path = tmp_path / "no_track.ipynb"
    nb = nbformat.v4.new_notebook()
    nbformat.write(nb, nb_path)

    kernel = _make_mock_kernel()
    transport = make_document_transport(
        mode="local",
        local_path=str(nb_path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
    )
    session = NotebookSession(kernel=kernel, doc=transport)
    await session.start()

    mock_report = {
        "success": True,
        "report": {"pandas": {"pip": "pandas", "success": True}},
    }
    with patch(
        "agent_jupyter_toolkit.utils.packages.ensure_packages_with_report",
        new=AsyncMock(return_value=mock_report),
    ):
        result = await session.install_packages(["pandas"], track=False)

    assert result["success"] is True
    assert result["tracked"] == []

    deps = await session.get_tracked_dependencies()
    assert deps == {}


async def test_uninstall_packages_removes_from_metadata(tmp_path):
    """uninstall_packages() should uninstall and remove tracked entry."""
    nb_path = tmp_path / "uninstall.ipynb"
    nb = nbformat.v4.new_notebook()
    nb.metadata["agent_dependencies"] = {
        "pandas": {"version": "2.1.0", "installed_at": "2024-01-15T00:00:00+00:00"},
        "numpy": {"version": "1.26.0", "installed_at": "2024-01-15T00:00:00+00:00"},
    }
    nbformat.write(nb, nb_path)

    kernel = _make_mock_kernel()
    transport = make_document_transport(
        mode="local",
        local_path=str(nb_path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
    )
    session = NotebookSession(kernel=kernel, doc=transport)
    await session.start()

    mock_uninstall_report = {
        "success": True,
        "report": {
            "pandas": {"pip": "pandas", "was_installed": True, "uninstalled": True, "error": None},
        },
    }
    with patch(
        "agent_jupyter_toolkit.utils.packages.uninstall_packages",
        new=AsyncMock(return_value=mock_uninstall_report),
    ):
        result = await session.uninstall_packages(["pandas"])

    assert result["success"] is True
    assert "pandas" in result["untracked"]

    # pandas removed, numpy still there
    deps = await session.get_tracked_dependencies()
    assert "pandas" not in deps
    assert "numpy" in deps


async def test_get_tracked_dependencies_empty_notebook(tmp_path):
    """get_tracked_dependencies() should return empty dict for new notebooks."""
    nb_path = tmp_path / "empty.ipynb"
    nb = nbformat.v4.new_notebook()
    nbformat.write(nb, nb_path)

    kernel = _make_mock_kernel()
    transport = make_document_transport(
        mode="local",
        local_path=str(nb_path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
    )
    session = NotebookSession(kernel=kernel, doc=transport)
    await session.start()

    deps = await session.get_tracked_dependencies()
    assert deps == {}


async def test_install_accumulates_dependencies(tmp_path):
    """Multiple install_packages() calls should accumulate in metadata."""
    nb_path = tmp_path / "accum.ipynb"
    nb = nbformat.v4.new_notebook()
    nbformat.write(nb, nb_path)

    kernel = _make_mock_kernel()
    transport = make_document_transport(
        mode="local",
        local_path=str(nb_path),
        remote_base=None,
        remote_path=None,
        token=None,
        headers_json=None,
    )
    session = NotebookSession(kernel=kernel, doc=transport)
    await session.start()

    # Install round 1
    with (
        patch(
            "agent_jupyter_toolkit.utils.packages.ensure_packages_with_report",
            new=AsyncMock(return_value={"success": True, "report": {"pandas": {"success": True}}}),
        ),
        patch(
            "agent_jupyter_toolkit.utils.packages.get_package_versions",
            new=AsyncMock(return_value={"pandas": "2.1.0"}),
        ),
    ):
        await session.install_packages(["pandas"])

    # Install round 2
    with (
        patch(
            "agent_jupyter_toolkit.utils.packages.ensure_packages_with_report",
            new=AsyncMock(return_value={"success": True, "report": {"plotly": {"success": True}}}),
        ),
        patch(
            "agent_jupyter_toolkit.utils.packages.get_package_versions",
            new=AsyncMock(return_value={"plotly": "5.18.0"}),
        ),
    ):
        await session.install_packages(["plotly"])

    deps = await session.get_tracked_dependencies()
    assert len(deps) == 2
    assert deps["pandas"]["version"] == "2.1.0"
    assert deps["plotly"]["version"] == "5.18.0"
