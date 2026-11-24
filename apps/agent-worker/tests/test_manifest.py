import pytest
from app.commerce.service import verify_manifest_payload
from app.schemas import CartIntentCreate, MerchantManifest
from app.signing import canonical_json
from oacp_merchant import build_manifest, decode_b64url, generate_keystore
from oacp_spec import validate
from pydantic import ValidationError

MERCHANT_ID = "demo-merchant"
SIGNING_KEY_ID = "merchant-key-1"
CATEGORY = "computer_software_stores"


def _signed_manifest() -> tuple[dict, dict[str, str]]:
    keystore = generate_keystore(merchant_id=MERCHANT_ID, signing_key_id=SIGNING_KEY_ID)
    manifest = build_manifest(
        merchant_id=MERCHANT_ID,
        merchant_name="Demo Merchant",
        origin="https://shop.example.com",
        checkout_endpoint="https://shop.example.com/checkout",
        currency="usd",
        mcc_category=CATEGORY,
        signing_key_id=SIGNING_KEY_ID,
        ed25519_private_key=keystore["ed25519_private_key"],
    )
    return manifest, keystore


def _cart(manifest: dict, **overrides: object) -> dict:
    body: dict = {
        "merchant_id": MERCHANT_ID,
        "order_id": "order-1",
        "amount_minor": 2598,
        "currency": "usd",
        "merchant_category": CATEGORY,
        "lines": [{"sku": "sku-1", "title": "One", "quantity": 2, "unit_amount_minor": 1299}],
        "merchant_manifest": manifest,
    }
    body.update(overrides)
    return body


def test_signed_manifest_conforms_to_the_normative_schema() -> None:
    manifest, _ = _signed_manifest()
    validate("merchant_manifest", manifest)


def test_server_verifies_the_signature_over_the_received_bytes() -> None:
    manifest, keystore = _signed_manifest()
    verify_manifest_payload(manifest, decode_b64url(keystore["ed25519_public_key"]))


def test_model_normalisation_does_not_change_the_signed_bytes() -> None:
    manifest, _ = _signed_manifest()
    dumped = MerchantManifest.model_validate(manifest).model_dump(mode="json")
    unsigned = {key: value for key, value in manifest.items() if key != "signature"}
    assert canonical_json({key: value for key, value in dumped.items() if key != "signature"}) == canonical_json(unsigned)


def test_tampered_manifest_is_rejected() -> None:
    manifest, keystore = _signed_manifest()
    with pytest.raises(PermissionError):
        verify_manifest_payload({**manifest, "merchant_name": "Someone Else"}, decode_b64url(keystore["ed25519_public_key"]))


def test_unsigned_manifest_is_rejected() -> None:
    manifest, keystore = _signed_manifest()
    unsigned = {key: value for key, value in manifest.items() if key != "signature"}
    with pytest.raises(ValueError):
        verify_manifest_payload(unsigned, decode_b64url(keystore["ed25519_public_key"]))


def test_manifest_requires_https() -> None:
    manifest, _ = _signed_manifest()
    with pytest.raises(ValidationError):
        MerchantManifest.model_validate({**manifest, "origin": "http://shop.example.com"})


def test_cart_intent_accepts_matching_totals() -> None:
    manifest, _ = _signed_manifest()
    assert CartIntentCreate.model_validate(_cart(manifest)).currency == "usd"


def test_cart_intent_rejects_total_mismatch() -> None:
    manifest, _ = _signed_manifest()
    with pytest.raises(ValidationError):
        CartIntentCreate.model_validate(_cart(manifest, amount_minor=2599))


def test_cart_intent_rejects_unaccepted_currency() -> None:
    manifest, _ = _signed_manifest()
    with pytest.raises(ValidationError):
        CartIntentCreate.model_validate(_cart(manifest, currency="eur"))


def test_cart_intent_rejects_category_mismatch() -> None:
    manifest, _ = _signed_manifest()
    with pytest.raises(ValidationError):
        CartIntentCreate.model_validate(_cart(manifest, merchant_category="grocery_stores"))


def test_cart_intent_rejects_merchant_mismatch() -> None:
    manifest, _ = _signed_manifest()
    with pytest.raises(ValidationError):
        CartIntentCreate.model_validate(_cart(manifest, merchant_id="other-merchant"))
