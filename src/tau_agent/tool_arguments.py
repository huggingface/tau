"""Check a tool call's arguments against the JSON schema its tool declares.

Covers the keywords Tau's tools use: `type`, `required`, `properties`,
`additionalProperties`, `enum` and `items`. Other keywords are ignored. An
optional argument sent as null counts as not sent. Arguments are only checked,
never coerced.
"""

from __future__ import annotations

from collections.abc import Mapping

from tau_agent.types import JSONValue

_TYPE_NAMES = ("string", "integer", "number", "boolean", "array", "object", "null")


def argument_problems(
    schema: Mapping[str, JSONValue], arguments: Mapping[str, JSONValue]
) -> list[str]:
    """Every way `arguments` breaks `schema`, each naming the argument's path."""
    problems: list[str] = []
    _check(schema, arguments, "", problems)
    return problems


def invalid_arguments_message(tool_name: str, problems: list[str]) -> str:
    """The one error a model gets for a call whose arguments do not fit."""
    return (
        f"Invalid arguments for {tool_name}: {'; '.join(problems)}. "
        "Send the call again with these fixed."
    )


def _check(schema: object, value: object, path: str, problems: list[str]) -> None:
    if not isinstance(schema, Mapping):
        return
    expected = _expected_types(schema.get("type"))
    if expected and not any(_is_type(value, name) for name in expected):
        problems.append(f"{_where(path)} must be {' or '.join(expected)}, got {_type_of(value)}")
        return
    enum = schema.get("enum")
    if isinstance(enum, list) and not any(_same(value, option) for option in enum):
        options = ", ".join(_show(option) for option in enum)
        problems.append(f"{_where(path)} must be one of {options}, got {_show(value)}")
        return
    if isinstance(value, Mapping):
        _check_object(schema, value, path, problems)
    elif isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                _check(items, item, f"{path}[{index}]", problems)


def _check_object(
    schema: Mapping[str, object], value: Mapping[str, object], path: str, problems: list[str]
) -> None:
    required = schema.get("required")
    required_keys = (
        [key for key in required if isinstance(key, str)] if isinstance(required, list) else []
    )
    for key in required_keys:
        if key not in value:
            problems.append(f'missing required "{_join(path, key)}"')
    properties = schema.get("properties")
    declared = properties if isinstance(properties, Mapping) else {}
    extra = schema.get("additionalProperties", True)
    for key, item in value.items():
        if item is None and key not in required_keys:
            continue
        if key in declared:
            _check(declared[key], item, _join(path, key), problems)
        elif extra is False:
            problems.append(f'unknown argument "{_join(path, key)}"')
        elif isinstance(extra, Mapping):
            _check(extra, item, _join(path, key), problems)


def _expected_types(declared: object) -> list[str]:
    names = declared if isinstance(declared, list) else [declared]
    return [name for name in names if isinstance(name, str) and name in _TYPE_NAMES]


def _is_type(value: object, name: str) -> bool:
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        if isinstance(value, bool):
            return False
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, Mapping)
    return value is None


def _type_of(value: object) -> str:
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _is_type(value, name):
            return name
    return type(value).__name__


def _same(value: object, option: object) -> bool:
    # JSON equality: true is not 1, and 1 is 1.0.
    if isinstance(value, bool) or isinstance(option, bool):
        return isinstance(value, bool) and isinstance(option, bool) and value == option
    return value == option


def _show(value: object) -> str:
    if isinstance(value, str):
        return f'"{value}"'
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return _type_of(value)


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _where(path: str) -> str:
    return f'"{path}"' if path else "the arguments"
