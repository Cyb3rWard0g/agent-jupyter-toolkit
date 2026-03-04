"""
Factory functions for creating kernel sessions and notebook transports.

This module provides simplified constructors that follow common patterns
and use sensible defaults for typical AI agent workflows.
"""

import logging

from ..kernel import ServerConfig, SessionConfig, create_session
from ..notebook import make_document_transport
from ..notebook.transport import NotebookDocumentTransport

logger = logging.getLogger(__name__)


def create_kernel(
    mode: str = "local",
    *,
    # Local options
    kernel_name: str = "python3",
    connection_file: str | None = None,
    packer: str | None = None,
    # Remote options
    base_url: str | None = None,
    token: str | None = None,
    notebook_path: str | None = None,
    headers: dict[str, str] | None = None,
):
    """
    Create a kernel session with simplified parameters.

    Args:
        mode: "local" or "remote"
        kernel_name: Kernel type (default: "python3")
        connection_file: For local mode, existing connection file
        packer: Optional serializer name for jupyter_client.Session
        base_url: For remote mode, Jupyter server URL
        token: For remote mode, API token
        notebook_path: For remote mode, bind to specific notebook
        headers: For remote mode, extra HTTP headers

    Returns:
        Configured kernel session ready for execution

    Examples:
        # Local kernel
        kernel = create_kernel("local")

        # Remote kernel
        kernel = create_kernel(
            "remote",
            base_url="http://localhost:8888",
            token="abc123"
        )
    """
    if mode == "local":
        logger.debug(f"Creating local kernel session with kernel_name={kernel_name}")
        return create_session(
            SessionConfig(
                mode="local",
                kernel_name=kernel_name,
                connection_file_name=connection_file,
                packer=packer,
            )
        )
    elif mode == "remote":
        if not base_url:
            logger.error("base_url required for remote mode")
            raise ValueError("base_url required for remote mode")
        logger.debug(f"Creating remote kernel session for {base_url}")
        return create_session(
            SessionConfig(
                mode="server",
                server=ServerConfig(
                    base_url=base_url,
                    token=token,
                    headers=headers,
                    kernel_name=kernel_name,
                    notebook_path=notebook_path,
                ),
            )
        )
    else:
        logger.error(f"Unknown mode: {mode}")
        raise ValueError(f"Unknown mode: {mode}")


def create_notebook_transport(
    mode: str,
    path: str,
    *,
    # Remote options
    base_url: str | None = None,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    # Collaboration options
    prefer_collab: bool = False,
    create_if_missing: bool = True,
    local_autosave_delay: float | None = None,
) -> NotebookDocumentTransport:
    """
    Create a notebook document transport with simplified parameters.

    Args:
        mode: "local" or "remote"
        path: Notebook file path
        base_url: For remote mode, Jupyter server URL
        token: For remote mode, API token
        headers: For remote mode, extra HTTP headers
        prefer_collab: Use collaborative transport if available
        create_if_missing: Create notebook if it doesn't exist
        local_autosave_delay: Optional debounce delay (seconds) for local writes

    Returns:
        Configured document transport

    Examples:
        # Local notebook
        doc = create_notebook_transport("local", "analysis.ipynb")

        # Remote notebook (Contents API)
        doc = create_notebook_transport(
            "remote", "analysis.ipynb",
            base_url="http://localhost:8888",
            token="abc123"
        )

        # Collaborative notebook
        doc = create_notebook_transport(
            "remote", "shared.ipynb",
            base_url="http://localhost:8888",
            prefer_collab=True
        )
    """
    if mode == "local":
        logger.debug(f"Creating local document transport for path: {path}")
        return make_document_transport(
            mode="local",
            local_path=path,
            remote_base=None,
            remote_path=None,
            token=None,
            headers_json=None,
            prefer_collab=False,
            local_autosave_delay=local_autosave_delay,
        )
    elif mode == "remote":
        if not base_url:
            logger.error("base_url required for remote mode")
            raise ValueError("base_url required for remote mode")

        logger.debug(f"Creating remote document transport for {base_url}/{path}")
        headers_json = None
        if headers:
            import json

            headers_json = json.dumps(headers)

        return make_document_transport(
            mode="server",
            local_path=None,
            remote_base=base_url,
            remote_path=path,
            token=token,
            headers_json=headers_json,
            prefer_collab=prefer_collab,
            create_if_missing=create_if_missing,
            local_autosave_delay=local_autosave_delay,
        )
    else:
        logger.error(f"Unknown mode: {mode}")
        raise ValueError(f"Unknown mode: {mode}")
