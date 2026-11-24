from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.commerce.service import _checkout_token, seal_checkout_envelope
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from oacp_merchant import decode_b64url, decrypt_envelope, generate_keystore
from oacp_spec import validate

MERCHANT_ID = "demo-merchant"


def _merchant_key(keystore: dict[str, str]) -> X25519PrivateKey:
    return X25519PrivateKey.from_private_bytes(decode_b64url(keystore["x25519_private_key"]))


def _envelope(token_id, merchant_key: X25519PrivateKey) -> dict:
    associated_data = {
        "protocol_version": "oacp/0.1",
        "token_id": str(token_id),
        "merchant_id": MERCHANT_ID,
        "order_id": "order-1",
        "amount_minor": 2598,
        "currency": "usd",
        "issued_at": datetime.now(UTC).isoformat(),
    }
    sealed = seal_checkout_envelope(
        token_id=token_id,
        merchant_public_key=merchant_key.public_key(),
        plaintext={"pan": "4242424242424242", "cvc": "123", "exp_month": 4, "exp_year": 2030},
        associated_data=associated_data,
    )
    return {
        "protocol_version": "oacp/0.1",
        "token_id": str(token_id),
        **sealed,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    }


def test_checkout_token_is_deterministic_and_prefixed() -> None:
    intent_id = uuid4()
    expires = datetime.now(UTC) + timedelta(minutes=5)
    token = _checkout_token(intent_id, "user-1", MERCHANT_ID, expires)
    assert token.startswith("oacp_tok_")
    assert token == _checkout_token(intent_id, "user-1", MERCHANT_ID, expires)


def test_checkout_token_binds_expiry_user_and_merchant() -> None:
    intent_id = uuid4()
    expires = datetime.now(UTC) + timedelta(minutes=5)
    token = _checkout_token(intent_id, "user-1", MERCHANT_ID, expires)
    assert token != _checkout_token(intent_id, "user-1", MERCHANT_ID, expires + timedelta(seconds=1))
    assert token != _checkout_token(intent_id, "user-2", MERCHANT_ID, expires)
    assert token != _checkout_token(intent_id, "user-1", "other-merchant", expires)


def test_checkout_token_matches_the_exchange_schema() -> None:
    token = _checkout_token(uuid4(), "user-1", MERCHANT_ID, datetime.now(UTC) + timedelta(minutes=5))
    validate(
        "checkout_token_exchange",
        {
            "protocol_version": "oacp/0.1",
            "checkout_token": token,
            "merchant_id": MERCHANT_ID,
            "merchant_x25519_public_key": "a" * 43,
            "idempotency_key": "idem-" + "x" * 16,
        },
    )


def test_envelope_round_trip_matches_the_response_schema() -> None:
    keystore = generate_keystore(merchant_id=MERCHANT_ID, signing_key_id="merchant-key-1")
    merchant_key = _merchant_key(keystore)
    response = _envelope(uuid4(), merchant_key)
    validate("checkout_token_exchange_response", response)
    card = decrypt_envelope(response, merchant_key)
    assert card["pan"] == "4242424242424242"
    assert card["cvc"] == "123"


def test_envelope_only_opens_with_the_addressed_merchant_key() -> None:
    response = _envelope(uuid4(), _merchant_key(generate_keystore(merchant_id=MERCHANT_ID, signing_key_id="k1")))
    other = _merchant_key(generate_keystore(merchant_id=MERCHANT_ID, signing_key_id="k2"))
    with pytest.raises(InvalidTag):
        decrypt_envelope(response, other)


def test_envelope_associated_data_tamper_is_detected() -> None:
    merchant_key = _merchant_key(generate_keystore(merchant_id=MERCHANT_ID, signing_key_id="k1"))
    response = _envelope(uuid4(), merchant_key)
    tampered = {**response, "associated_data": str(response["associated_data"]).replace("order-1", "order-2")}
    with pytest.raises(InvalidTag):
        decrypt_envelope(tampered, merchant_key)
