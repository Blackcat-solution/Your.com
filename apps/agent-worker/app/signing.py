"""Canonical JSON and Ed25519 signature helpers shared by every approval flow.

Both sides of a signature must produce the same bytes. NexusRelay defines them
as ``json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)``
encoded as UTF-8, so the browser, the API and a merchant all agree.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

SIGNATURE_BYTES = 64


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_bytes(value: dict[str, Any]) -> bytes:
    return canonical_json(value).encode()


def decode_signature(value: str) -> bytes:
    """Decode a standard base64 Ed25519 signature, raising ValueError otherwise."""
    try:
        signature = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise ValueError("Signature must be base64 encoded") from exc
    if len(signature) != SIGNATURE_BYTES:
        raise ValueError("Signature must be a 64-byte Ed25519 signature")
    return signature


def verify_signature(public_key: bytes, value: dict[str, Any], signature: bytes) -> bool:
    """Return True only for a valid signature over the canonical form of value."""
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, canonical_bytes(value))
    except (InvalidSignature, ValueError):
        return False
    return True
