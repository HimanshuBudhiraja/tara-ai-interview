"""A very small JSON Schema checker.

Supports exactly what the AI schemas in this package use: object/array/string/
number/integer/boolean/null types, `required`, `enum`, `items`, `properties`,
`minimum`/`maximum`, `minItems`/`maxItems`. Anything else in a schema is
ignored rather than guessed at.

Errors name the path (`skills[2].priority`) because a workload failure that
says "invalid response" costs someone twenty minutes with a debugger.
"""
from __future__ import annotations

from typing import Any

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "null": (type(None),),
}


class SchemaError(ValueError):
    """The model returned something the schema does not allow."""


def validate(value: Any, schema: dict[str, Any], path: str = "") -> None:
    """Raise `SchemaError` on the first violation, naming where it is."""
    where = path or "<root>"

    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        allowed: tuple[type, ...] = tuple(t for name in types for t in _TYPES.get(name, ()))
        # bool is a subclass of int in Python; a schema asking for a number
        # should not silently accept True.
        if allowed and (
            not isinstance(value, allowed)
            or (isinstance(value, bool) and "boolean" not in types)
        ):
            raise SchemaError(f"{where}: expected {'/'.join(types)}, got {type(value).__name__}")

    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{where}: {value!r} is not one of {schema['enum']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaError(f"{where}: {value} is below the minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaError(f"{where}: {value} is above the maximum {schema['maximum']}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SchemaError(f"{where}: needs at least {schema['minItems']} items, got {len(value)}")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaError(f"{where}: allows at most {schema['maxItems']} items, got {len(value)}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, entry in enumerate(value):
                validate(entry, item_schema, f"{path}[{i}]")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise SchemaError(f"{where}: missing required key {key!r}")
        for key, sub in (schema.get("properties") or {}).items():
            if key in value:
                validate(value[key], sub, f"{path}.{key}" if path else key)
