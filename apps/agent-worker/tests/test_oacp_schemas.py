import pytest
from jsonschema import Draft202012Validator
from oacp_spec import (
    SCHEMA_NAMES,
    OACPValidationError,
    canonical_json,
    load_schema,
    schema_directory,
    validate,
)


def test_every_schema_is_valid_draft_2020_12() -> None:
    for name in SCHEMA_NAMES:
        Draft202012Validator.check_schema(load_schema(name))


def test_schema_directory_holds_exactly_the_normative_files() -> None:
    directory = schema_directory()
    assert (directory / "merchant_manifest.json").is_file()
    assert sorted(path.stem for path in directory.glob("*.json")) == sorted(SCHEMA_NAMES)


def test_canonical_json_is_stable() -> None:
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_validation_error_reports_the_offending_path() -> None:
    with pytest.raises(OACPValidationError) as error:
        validate("jsonrpc_envelope", {"jsonrpc": "2.0", "id": 1, "method": "cart.create", "params": {}, "extra": True})
    assert "extra" in str(error.value)


def test_jsonrpc_envelope_accepts_every_defined_method() -> None:
    for method in ["merchant.manifest.get", "inventory.search", "cart.create", "cart.quote", "checkout.token.exchange"]:
        validate("jsonrpc_envelope", {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}})


def test_cart_intent_schema_accepts_a_valid_intent() -> None:
    validate(
        "cart_intent",
        {
            "protocol_version": "oacp/0.1",
            "intent_id": "8f6c3f0e-0f6c-4b1c-9c1a-1f6f2b8a9d10",
            "merchant_id": "demo-merchant",
            "order_id": "order-1",
            "currency": "usd",
            "amount_minor": 2598,
            "merchant_category": "computer_software_stores",
            "merchant_manifest_sha256": "a" * 64,
            "items": [{"sku": "sku-1", "title": "One", "quantity": 2, "unit_amount_minor": 1299}],
            "expires_at": "2030-01-01T00:00:00+00:00",
        },
    )
