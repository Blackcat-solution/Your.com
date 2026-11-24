"""Merchant-side reference implementation of the OACP 0.1 checkout exchange.

The merchant holds three secrets: an Ed25519 key that signs its manifest, an
X25519 key that decrypts payment envelopes, and an exchange credential that
authenticates it to the payment service. None of them leave the merchant.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from oacp_spec import PROTOCOL_VERSION, canonical_json, validate

HKDF_INFO = b"nexusrelay-oacp-0.1-checkout"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def decode_b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def generate_keystore(*, merchant_id: str, signing_key_id: str) -> dict[str, str]:
    """Create the merchant's signing key, decryption key and exchange credential."""
    signing_key = Ed25519PrivateKey.generate()
    exchange_key = X25519PrivateKey.generate()
    return {
        "merchant_id": merchant_id,
        "signing_key_id": signing_key_id,
        "ed25519_private_key": b64url(signing_key.private_bytes_raw()),
        "ed25519_public_key": b64url(signing_key.public_key().public_bytes_raw()),
        "x25519_private_key": b64url(exchange_key.private_bytes_raw()),
        "x25519_public_key": b64url(exchange_key.public_key().public_bytes_raw()),
        "exchange_secret": secrets.token_urlsafe(48),
    }


def save_keystore(path: str | Path, keystore: dict[str, str]) -> Path:
    target = Path(path)
    target.write_text(json.dumps(keystore, indent=2) + "\n", encoding="utf-8")
    if os.name == "posix":
        target.chmod(0o600)
    return target


def load_keystore(path: str | Path) -> dict[str, str]:
    keystore: dict[str, str] = json.loads(Path(path).read_text(encoding="utf-8"))
    return keystore


def sign_manifest(manifest: dict[str, Any], ed25519_private_key: str) -> str:
    unsigned = {key: value for key, value in manifest.items() if key != "signature"}
    key = Ed25519PrivateKey.from_private_bytes(decode_b64url(ed25519_private_key))
    return b64url(key.sign(canonical_json(unsigned).encode()))


def build_manifest(
    *,
    merchant_id: str,
    merchant_name: str,
    origin: str,
    checkout_endpoint: str,
    currency: str,
    mcc_category: str,
    signing_key_id: str,
    ed25519_private_key: str,
) -> dict[str, Any]:
    """Build and sign a manifest that matches the normative schema byte for byte."""
    manifest: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "merchant_id": merchant_id,
        "merchant_name": merchant_name,
        "origin": origin.rstrip("/"),
        "checkout_endpoint": checkout_endpoint,
        "accepted_currencies": [currency.upper()],
        "mcc_category": mcc_category,
        "payment_capabilities": ["oacp_checkout_token_v1"],
        "signing_key_id": signing_key_id,
    }
    manifest["signature"] = sign_manifest(manifest, ed25519_private_key)
    return validate("merchant_manifest", manifest)


def register_trust(
    *,
    api_url: str,
    merchant_id: str,
    admin_token: str,
    origin: str,
    signing_key_id: str,
    signing_public_key: str,
    exchange_secret: str,
) -> dict[str, Any]:
    """Register the merchant origin, manifest key and exchange credential."""
    with httpx.Client(timeout=20) as client:
        response = client.put(
            f"{api_url.rstrip('/')}/v1/commerce/merchants/{merchant_id}/trust",
            json={
                "origin": origin.rstrip("/"),
                "signing_key_id": signing_key_id,
                "signing_public_key": signing_public_key,
                "exchange_secret": exchange_secret,
            },
            headers={"X-OACP-Admin-Token": admin_token},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
    return body


def decrypt_envelope(envelope: dict[str, Any], merchant_private_key: X25519PrivateKey) -> dict[str, Any]:
    """Open a payment envelope addressed to this merchant's X25519 key."""
    token_id = UUID(str(envelope["token_id"]))
    shared = merchant_private_key.exchange(
        X25519PublicKey.from_public_bytes(decode_b64url(str(envelope["ephemeral_public_key"])))
    )
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=token_id.bytes, info=HKDF_INFO).derive(shared)
    plaintext = AESGCM(key).decrypt(
        decode_b64url(str(envelope["nonce"])),
        decode_b64url(str(envelope["ciphertext"])),
        str(envelope["associated_data"]).encode(),
    )
    card: dict[str, Any] = json.loads(plaintext)
    return card


def redeem_checkout_token(
    *,
    api_url: str,
    merchant_id: str,
    checkout_token: str,
    exchange_secret: str,
    x25519_private_key: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Exchange a one-time checkout token and decrypt the card inside the merchant."""
    key = X25519PrivateKey.from_private_bytes(decode_b64url(x25519_private_key))
    body = {
        "protocol_version": PROTOCOL_VERSION,
        "checkout_token": checkout_token,
        "merchant_id": merchant_id,
        "merchant_x25519_public_key": b64url(key.public_key().public_bytes_raw()),
        "idempotency_key": idempotency_key,
    }
    with httpx.Client(timeout=20) as client:
        response = client.post(
            f"{api_url.rstrip('/')}/v1/commerce/checkout-token/exchange",
            json=body,
            headers={"X-Merchant-Token": exchange_secret},
        )
        response.raise_for_status()
        envelope: dict[str, Any] = response.json()
    validate("checkout_token_exchange_response", envelope)
    return decrypt_envelope(envelope, key)
