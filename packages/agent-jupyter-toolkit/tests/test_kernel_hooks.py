"""
Test kernel hooks registration and triggering.
"""

from agent_jupyter_toolkit.kernel.hooks import kernel_hooks


def test_kernel_hooks():
    events = []

    def output_hook(msg):
        events.append(("output", msg))

    def before_hook(code):
        events.append(("before", code))

    def after_hook(result):
        events.append(("after", result))

    def on_error_hook(err):
        events.append(("error", str(err)))

    kernel_hooks.register_output_hook(output_hook)
    kernel_hooks.register_before_execute_hook(before_hook)
    kernel_hooks.register_after_execute_hook(after_hook)
    kernel_hooks.register_on_error_hook(on_error_hook)
    # Trigger hooks
    kernel_hooks.trigger_output_hooks({"foo": "bar"})
    kernel_hooks.trigger_before_execute_hooks("print(123)")
    kernel_hooks.trigger_after_execute_hooks({"result": 42})
    kernel_hooks.trigger_on_error_hooks(Exception("test error"))
    # Unregister
    kernel_hooks.unregister_output_hook(output_hook)
    kernel_hooks.unregister_before_execute_hook(before_hook)
    kernel_hooks.unregister_after_execute_hook(after_hook)
    kernel_hooks.unregister_on_error_hook(on_error_hook)
    assert any(e[0] == "output" for e in events)
    assert any(e[0] == "before" for e in events)
    assert any(e[0] == "after" for e in events)
    assert any(e[0] == "error" for e in events)


if __name__ == "__main__":
    test_kernel_hooks()
