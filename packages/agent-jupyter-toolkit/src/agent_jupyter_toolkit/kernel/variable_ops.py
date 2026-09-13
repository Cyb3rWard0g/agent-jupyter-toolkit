"""
Central registry for variable operation code templates (variable ops) for multiple languages.

This module provides a registry system for storing language-specific code templates
that can be used to perform variable operations like listing, getting, and setting
variables in different kernel languages.
"""


class VariableOpsRegistry:
    """
    Registry for storing language-specific code templates for variable operations.

    Supports 'list', 'get', and 'set' operations across different programming languages.
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self.ops: dict[str, dict[str, str]] = {}

    def register(
        self,
        language: str,
        list_code: str,
        get_code: str,
        set_code: str,
        *,
        list_detailed_code: str | None = None,
    ) -> None:
        """
        Register code templates for a programming language.

        Args:
            language: The programming language identifier (e.g., "python", "r")
            list_code: Code template to list all variables
            get_code: Code template to get a variable (should contain {name} placeholder)
            set_code: Code template to set a variable (should contain {name} and {value}
                placeholders)
            list_detailed_code: Optional code template to list variables with metadata
        """
        ops = {
            "list": list_code,
            "get": get_code,
            "set": set_code,
        }
        if list_detailed_code:
            ops["list_detailed"] = list_detailed_code
        self.ops[language] = ops

    def get(self, language: str, op: str) -> str:
        """
        Retrieve a code template for a specific language and operation.

        Args:
            language: The programming language identifier
            op: The operation type ("list", "get", or "set")

        Returns:
            The code template string

        Raises:
            ValueError: If the language or operation is not registered
        """
        if language not in self.ops or op not in self.ops[language]:
            raise ValueError(f"No code template for {op} in language {language}")
        return self.ops[language][op]


# Global registry instance with pre-registered Python operations
VARIABLE_OPS = VariableOpsRegistry()

# Register Python variable operations with improved templates
VARIABLE_OPS.register(
    "python",
    list_code="""
import json
# Get user-defined variables (exclude built-ins and modules)
user_vars = [name for name, obj in globals().items()
             if not name.startswith('_')
             and not callable(obj)
             and not hasattr(obj, '__module__')]
print(json.dumps(user_vars))
""",
    get_code="import json; print(json.dumps(globals().get('{name}', None), default=str))",
    set_code="import json; {name} = json.loads('''{value}''')",
    list_detailed_code="""
def _ajt_describe_variables(_ajt_namespace):
    import json as _ajt_json
    import sys as _ajt_sys

    _ajt_vars = []
    for _ajt_name, _ajt_obj in list(_ajt_namespace.items()):
        if _ajt_name.startswith("_"):
            continue
        if callable(_ajt_obj) or hasattr(_ajt_obj, "__module__"):
            continue
        _ajt_type = type(_ajt_obj)
        _ajt_vars.append(
            {
                "name": _ajt_name,
                "type": [
                    getattr(_ajt_type, "__module__", None),
                    getattr(_ajt_type, "__qualname__", ""),
                ],
                "size": _ajt_sys.getsizeof(_ajt_obj),
            }
        )
    return _ajt_json.dumps(_ajt_vars)

print(_ajt_describe_variables(globals()))
del _ajt_describe_variables
""",
)
