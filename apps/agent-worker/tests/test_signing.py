import base64

import pytest
from app.signing import canonical_bytes, canonical_json, decode_signature, verify_signature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from oacp_spec import canonical_json as spec_canonical_json


def _keypair() -> tuple[bytes, bytes]:
    key = Ed25519PrivateKey.generate()
    return key.private_bytes_raw(), key.public_key().public_bytes_raw()


def test_canonical_json_sorts_keys_and_drops_whitespace() -> None:
    assert canonical_json({"b": 1, "a": {"d": [2], "c": "x"}}) == '{"a":{"c":"x","d":[2]},"b":1}'
    assert canonical_bytes({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_canonical_json_matches_the_spec_package() -> None:
    value = {"z": "e", "a": [1, {"b": None}], "unicode": "caf\u00e9"}
    assert canonical_json(value) == spec_canonical_json(value)


def test_signature_round_trip() -> None:
    private, public = _keypair()
    payload = {"approval_id": "a1", "run_id": "r1", "decision": "approve"}
    signature = Ed25519PrivateKey.from_private_bytes(private).sign(canonical_bytes(payload))
    assert verify_signature(public, payload, signature)


def test_signature_rejects_tampered_payload() -> None:
    private, public = _keypair()
    signature = Ed25519PrivateKey.from_private_bytes(private).sign(
        canonical_bytes({"decision": "approve", "amount_minor": 1299})
    )
    assert not verify_signature(public, {"decision": "approve", "amount_minor": 1300}, signature)


def test_signature_rejects_another_key() -> None:
    private, _ = _keypair()
    _, other_public = _keypair()
    signature = Ed25519PrivateKey.from_private_bytes(private).sign(canonical_bytes({"a": 1}))
    assert not verify_signature(other_public, {"a": 1}, signature)


def test_the_decision_must_be_signed() -> None:
    private, public = _keypair()
    challenge = {"approval_id": "a1", "nonce": "n"}
    signature = Ed25519PrivateKey.from_private_bytes(private).sign(
        canonical_bytes({**challenge, "decision": "approve"})
    )
    assert verify_signature(public, {**challenge, "decision": "approve"}, signature)
    assert not verify_signature(public, {**challenge, "decision": "reject"}, signature)
    assert not verify_signature(public, challenge, signature)


def test_decode_signature_validates_encoding_and_length() -> None:
    private, _ = _keypair()
    signature = Ed25519PrivateKey.from_private_bytes(private).sign(b"x")
    assert decode_signature(base64.b64encode(signature).decode()) == signature
    with pytest.raises(ValueError):
        decode_signature("not base64!!")
    with pytest.raises(ValueError):
        decode_signature(base64.b64encode(b"short").decode())
