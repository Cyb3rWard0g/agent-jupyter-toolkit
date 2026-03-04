"""
Kernel execution hooks and event callbacks for extensibility and monitoring.

This module provides a hook system that allows external code to register callbacks
for various kernel execution lifecycle events. Hooks are useful for logging,
monitoring, debugging, and extending kernel behavior.

Features:
    - Output hooks: Capture and process IOPub messages
    - Before/after execution hooks: Monitor code execution lifecycle
    - Error hooks: Handle and log execution errors
    - Thread-safe singleton instance for global access

Example:
    ```python
    from agent_jupyter_toolkit.kernel.hooks import kernel_hooks

    # Register a logging hook
    def log_output(msg):
        print(f"Output: {msg}")

    kernel_hooks.register_output_hook(log_output)

    # Execute code - hooks will be triggered automatically
    result = await session.execute("print('Hello')")
    ```
"""

from collections.abc import Callable


class KernelHooks:
    """
    Registry and dispatcher for kernel execution lifecycle hooks.

    This class provides a centralized way to register and trigger callbacks
    at various points during kernel code execution. All hook methods are
    thread-safe and handle exceptions gracefully.

    Hook Types:
        - output_hooks: Called for each IOPub message (stream, display_data, etc.)
        - before_execute_hooks: Called before code execution starts
        - after_execute_hooks: Called after code execution completes
        - on_error_hooks: Called when execution errors occur

    Example:
        ```python
        hooks = KernelHooks()

        def debug_output(msg):
            print(f"Debug: {msg.get('msg_type')}")

        hooks.register_output_hook(debug_output)
        ```
    """

    def __init__(self):
        self._output_hooks: list[Callable[[dict], None]] = []
        self._before_execute_hooks: list[Callable[[str], None]] = []
        self._after_execute_hooks: list[Callable[[object], None]] = []
        self._on_error_hooks: list[Callable[[Exception], None]] = []

    # Output hooks - process IOPub messages
    def register_output_hook(self, hook: Callable[[dict], None]) -> None:
        """
        Register a callback to process IOPub messages during execution.

        Args:
            hook: Function that accepts a Jupyter message dict
        """
        self._output_hooks.append(hook)

    def unregister_output_hook(self, hook: Callable[[dict], None]) -> None:
        """Remove a previously registered output hook."""
        if hook in self._output_hooks:
            self._output_hooks.remove(hook)

    def trigger_output_hooks(self, msg: dict) -> None:
        """
        Trigger all registered output hooks with an IOPub message.

        Args:
            msg: Jupyter protocol message dict
        """
        for hook in self._output_hooks:
            try:
                hook(msg)
            except Exception:
                # Silently ignore hook errors to prevent breaking execution
                pass

    # Before-execute hooks - called before code execution
    def register_before_execute_hook(self, hook: Callable[[str], None]) -> None:
        """
        Register a callback to be called before code execution.

        Args:
            hook: Function that accepts the code string to be executed
        """
        self._before_execute_hooks.append(hook)

    def unregister_before_execute_hook(self, hook: Callable[[str], None]) -> None:
        """Remove a previously registered before-execute hook."""
        if hook in self._before_execute_hooks:
            self._before_execute_hooks.remove(hook)

    def trigger_before_execute_hooks(self, code: str) -> None:
        """
        Trigger all registered before-execute hooks.

        Args:
            code: The code string about to be executed
        """
        for hook in self._before_execute_hooks:
            try:
                hook(code)
            except Exception:
                pass

    # After-execute hooks - called after execution completes
    def register_after_execute_hook(self, hook: Callable[[object], None]) -> None:
        """
        Register a callback to be called after code execution completes.

        Args:
            hook: Function that accepts the execution result
        """
        self._after_execute_hooks.append(hook)

    def unregister_after_execute_hook(self, hook: Callable[[object], None]) -> None:
        """Remove a previously registered after-execute hook."""
        if hook in self._after_execute_hooks:
            self._after_execute_hooks.remove(hook)

    def trigger_after_execute_hooks(self, result: object) -> None:
        """
        Trigger all registered after-execute hooks.

        Args:
            result: The execution result object
        """
        for hook in self._after_execute_hooks:
            try:
                hook(result)
            except Exception:
                pass

    # Error hooks - called when execution errors occur
    def register_on_error_hook(self, hook: Callable[[Exception], None]) -> None:
        """
        Register a callback to be called when execution errors occur.

        Args:
            hook: Function that accepts the exception instance
        """
        self._on_error_hooks.append(hook)

    def unregister_on_error_hook(self, hook: Callable[[Exception], None]) -> None:
        """Remove a previously registered error hook."""
        if hook in self._on_error_hooks:
            self._on_error_hooks.remove(hook)

    def trigger_on_error_hooks(self, error: Exception) -> None:
        """
        Trigger all registered error hooks.

        Args:
            error: The exception that occurred during execution
        """
        for hook in self._on_error_hooks:
            try:
                hook(error)
            except Exception:
                pass


# Global singleton instance for convenient access across the codebase
kernel_hooks = KernelHooks()
