"""OACP 0.1 schemas and validation helpers.

The JSON Schemas in ``packages/oacp-spec/schemas`` are normative. This module
loads them so the agent worker and a merchant implementation validate the same
definition of every wire object.

Signatures cover :func:`canonical_json` of the object: sorted keys, no
insignificant whitespace, UTF-8. A merchant manifest signature covers the
manifest exactly as submitted, excluding the ``signature`` member.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from jsonschema import Draft202012Validator

PROTOCOL_VERSION: Final[str] = "oacp/0.1"
SCHEMA_NAMES: Final[tuple[str, ...]] = (
    "cart_intent",
    "checkout_token_exchange",
    "checkout_token_exchange_response",
    "jsonrpc_envelope",
    "merchant_manifest",
)


class OACPValidationError(ValueError):
    """Raised when a payload does not conform to the normative OACP schema."""


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def schema_directory() -> Path:
    """Locate the normative schema directory, honouring OACP_SCHEMA_DIR."""
    override = os.environ.get("OACP_SCHEMA_DIR")
    if override:
        directory = Path(override)
        if (directory / "merchant_manifest.json").is_file():
            return directory
        raise OACPValidationError(f"OACP_SCHEMA_DIR has no OACP schemas: {directory}")
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "schemas"
        if (candidate / "merchant_manifest.json").is_file():
            return candidate
    raise OACPValidationError("OACP schemas are not installed; set OACP_SCHEMA_DIR")


@lru_cache(maxsize=len(SCHEMA_NAMES))
def load_schema(name: str) -> dict[str, Any]:
    if name not in SCHEMA_NAMES:
        raise KeyError(f"Unknown OACP schema: {name}")
    return json.loads((schema_directory() / f"{name}.json").read_text(encoding="utf-8"))


def validate(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate payload against a normative schema and return it unchanged."""
    errors = sorted(
        Draft202012Validator(load_schema(name)).iter_errors(payload),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "<root>"
        raise OACPValidationError(f"{name} schema violation at {location}: {first.message}")
    return payload
