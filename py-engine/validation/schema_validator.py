"""JSON schema validation for Redis messages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent / "shared" / "schemas"

SchemaName = str  # "market-events" | "execution-orders" | "execution-results"

_schemas: dict[str, dict[str, Any]] = {}


def _load_schema(name: SchemaName) -> dict[str, Any]:
    if name not in _schemas:
        path = SCHEMA_DIR / f"{name}.schema.json"
        _schemas[name] = json.loads(path.read_text())
    return _schemas[name]


class SchemaValidationError(Exception):
    """Raised when a message fails schema validation."""

    def __init__(self, schema_name: str, errors: list[str]) -> None:
        self.schema_name = schema_name
        self.errors = errors
        super().__init__(f"Schema validation failed ({schema_name}): {'; '.join(errors)}")


def validate(schema_name: SchemaName, data: Any) -> tuple[bool, list[str]]:
    """Validate data against a named schema.

    Returns (valid, errors) tuple.
    """
    schema = _load_schema(schema_name)
    validator = jsonschema.Draft202012Validator(schema)
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '/'}: {e.message}"
        for e in validator.iter_errors(data)
    ]
    return (len(errors) == 0, errors)


def validate_or_raise(schema_name: SchemaName, data: Any) -> None:
    """Validate data and raise SchemaValidationError on failure."""
    valid, errors = validate(schema_name, data)
    if not valid:
        raise SchemaValidationError(schema_name, errors)
