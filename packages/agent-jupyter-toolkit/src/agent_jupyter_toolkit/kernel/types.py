"""
Canonical dataclasses and types for the kernel subsystem.
Used by both local (ZMQ) and remote (HTTP+WS) execution paths.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypedDict

# Type Aliases
OutputCallback = Callable[[list[dict[str, Any]], int | None], Awaitable[None]]


# Custom Exceptions
class KernelError(Exception):
    """Base exception for kernel-related errors."""

    pass


class KernelExecutionError(KernelError):
    """Raised when code execution fails in the kernel."""

    pass


class KernelTimeoutError(KernelError):
    """Raised when kernel operations exceed timeout."""

    pass


@dataclass
class ServerConfig:
    """
    Settings for connecting to a remote Jupyter Server.

    Attributes:
        base_url: Base URL to user's server (e.g., "https://hub.example.com/user/alex").
        token: API token for 'Authorization: Token <token>' (omit if using cookies).
        headers: Extra headers (e.g., {"Cookie":"...", "X-XSRFToken":"..."}).
        kernel_name: Kernel to create when launching a new session.
        notebook_path: If set, bind to a specific notebook via the Sessions API.
    """

    base_url: str
    token: str | None = None
    headers: dict[str, str] | None = None
    kernel_name: str = "python3"
    notebook_path: str | None = None


@dataclass
class SessionConfig:
    """
    High-level configuration for creating a kernel session.

    Attributes:
        mode: "local" or "server".
        kernel_name: Kernel name to spawn (default: "python3"). Used in local mode, and
                     as default when creating a server kernel/session unless overridden.
        connection_file_name: If provided in local mode, attach to an existing kernel.
        packer: Optional serializer name for jupyter_client.Session (e.g., "json", "orjson").
        server: Required when mode == "server"; settings for remote Jupyter Server.
    """

    mode: str = "local"
    kernel_name: str = "python3"
    connection_file_name: str | None = None
    packer: str | None = None
    server: ServerConfig | None = None


@dataclass
class ExecutionResult:
    """
    Normalized result of executing code in a kernel.

    Fields:
        status: "ok" or "error"
        execution_count: Kernel's execution counter if provided
        stdout/stderr: Concatenated stream text
        outputs: List of plain dicts (stream, display_data, execute_result, error) as required by
            nbformat

    Optional extras:
        user_expressions: Any user_expressions results
        elapsed_ms: Rough client-side timing if measured
    """

    status: str = "ok"
    execution_count: int | None = None
    stdout: str = ""
    stderr: str = ""
    # IMPORTANT: plain dicts, not dataclasses (nbformat requires JSONable objects)
    outputs: list[dict[str, Any]] = field(default_factory=list)

    # optional extras
    user_expressions: dict[str, Any] | None = None
    elapsed_ms: float | None = None


@dataclass
class CompleteResult:
    """
    Result of a tab-completion request.

    Attributes:
        matches: List of completion strings.
        cursor_start: Start offset of the text being completed.
        cursor_end: End offset of the text being completed.
        status: "ok" or "error".
        metadata: Extra metadata from the kernel (e.g., type annotations).
    """

    matches: list[str] = field(default_factory=list)
    cursor_start: int = 0
    cursor_end: int = 0
    status: str = "ok"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class InspectResult:
    """
    Result of an object-inspection request.

    Attributes:
        found: Whether the kernel found documentation for the object.
        data: MIME-keyed documentation (e.g., ``{"text/plain": "..."}``)
        metadata: Extra metadata from the kernel.
        status: "ok" or "error".
    """

    found: bool = False
    data: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"


@dataclass
class IsCompleteResult:
    """
    Result of a code-completeness check.

    Attributes:
        status: One of ``"complete"``, ``"incomplete"``, ``"invalid"``, or ``"unknown"``.
        indent: Suggested indentation if status is ``"incomplete"`` (e.g., ``"    "``).
    """

    status: str = "unknown"
    indent: str = ""


@dataclass
class HistoryEntry:
    """A single entry from kernel execution history."""

    session: int
    line_number: int
    input: str
    output: str | None = None


@dataclass
class HistoryResult:
    """
    Result of a history request.

    Attributes:
        history: List of history entries from the kernel.
        status: "ok" or "error".
    """

    history: list[HistoryEntry] = field(default_factory=list)
    status: str = "ok"


@dataclass
class KernelInfoResult:
    """
    Result of a kernel_info request.

    Attributes:
        protocol_version: Jupyter wire-protocol version (e.g., ``"5.4"``).
        implementation: Kernel implementation name (e.g., ``"ipython"``).
        implementation_version: Kernel implementation version string.
        language_info: Language metadata (name, version, mimetype, etc.).
        banner: Kernel startup banner text.
        status: "ok" or "error".
    """

    protocol_version: str = ""
    implementation: str = ""
    implementation_version: str = ""
    language_info: dict[str, Any] = field(default_factory=dict)
    banner: str = ""
    status: str = "ok"


class VariableDescription(TypedDict):
    """Metadata description for a kernel variable."""

    name: str
    type: tuple[str | None, str]
    size: int | None
