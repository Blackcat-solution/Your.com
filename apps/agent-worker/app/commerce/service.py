import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select

from app.db import ApprovalKey, MerchantCredential, PurchaseIntent, Session
from app.schemas import CartIntentCreate, CheckoutExchangeRequest, MerchantManifest
from app.settings import get_settings
from app.signing import canonical_json, decode_signature, verify_signature


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _decode_b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _checkout_token(intent_id: UUID, user_id: str, merchant_id: str, expires_at: datetime) -> str:
    key = get_settings().oacp_token_signing_key.encode()
    message = f"oacp:0.1:{intent_id}:{user_id}:{merchant_id}:{expires_at.isoformat()}".encode()
    if len(key) < 32 or b"replace-with" in key:
        raise RuntimeError("Set OACP_TOKEN_SIGNING_KEY to a random value with at least 32 bytes")
    return "oacp_tok_" + _b64url(hmac.new(key, message, hashlib.sha256).digest())


def _stripe_auth() -> tuple[str, str]:
    key = get_settings().stripe_secret_key
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is required for approved purchases")
    return key, ""


def verify_manifest_payload(manifest: dict[str, Any], signing_public_key: bytes) -> None:
    """Verify the Ed25519 signature over the manifest exactly as received."""
    signature_text = manifest.get("signature")
    if not isinstance(signature_text, str):
        raise ValueError("Merchant manifest is missing its signature")
    unsigned = {key: value for key, value in manifest.items() if key != "signature"}
    try:
        Ed25519PublicKey.from_public_bytes(signing_public_key).verify(
            _decode_b64url(signature_text), canonical_json(unsigned).encode()
        )
    except (InvalidSignature, ValueError) as exc:
        raise PermissionError("Merchant manifest signature is invalid") from exc


async def _verify_manifest(manifest_model: MerchantManifest) -> dict[str, Any]:
    manifest = manifest_model.model_dump(mode="json")
    origin = manifest_model.origin.rstrip("/")
    if not origin.startswith("https://") or not manifest_model.checkout_endpoint.startswith("https://"):
        raise ValueError("Merchant origin and checkout endpoint must use HTTPS")
    if urlsplit(origin).hostname != urlsplit(manifest_model.checkout_endpoint).hostname:
        raise ValueError("Checkout endpoint host must match the registered merchant origin")
    async with Session() as session:
        trust = await session.get(MerchantCredential, manifest_model.merchant_id)
        if not trust or not trust.enabled or trust.origin.rstrip("/") != origin or trust.signing_key_id != manifest_model.signing_key_id:
            raise PermissionError("Merchant manifest is not registered or its origin/key does not match")
        verify_manifest_payload(manifest, trust.signing_public_key)
    return manifest


async def create_cart_intent(user_id: str, body: CartIntentCreate) -> dict:
    manifest = await _verify_manifest(body.merchant_manifest)
    cart = body.model_dump(mode="json")
    manifest_hash = hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
    cart_hash = hashlib.sha256(canonical_json(cart).encode()).hexdigest()
    intent_id = UUID(bytes=secrets.token_bytes(16), version=4)
    created = datetime.now(UTC)
    expires = created + timedelta(minutes=15)
    payload = {
        "protocol_version": "oacp/0.1",
        "decision": "approve_purchase",
        "intent_id": str(intent_id),
        "user_id": user_id,
        "merchant_id": body.merchant_id,
        "merchant_name": body.merchant_manifest.merchant_name,
        "merchant_origin": str(body.merchant_manifest.origin),
        "order_id": body.order_id,
        "amount_minor": body.amount_minor,
        "currency": body.currency.lower(),
        "merchant_category": body.merchant_category,
        "manifest_sha256": manifest_hash,
        "cart_sha256": cart_hash,
        "expires_at": expires.isoformat(),
    }
    cart["_signing_payload"] = payload
    cart["_manifest_sha256"] = manifest_hash
    cart["_cart_sha256"] = cart_hash
    async with Session() as session:
        session.add(PurchaseIntent(
            id=intent_id,
            user_id=user_id,
            merchant_id=body.merchant_id,
            order_id=body.order_id,
            amount_minor=body.amount_minor,
            currency=body.currency.lower(),
            merchant_category=body.merchant_category,
            cart_payload=cart,
            status="pending",
            created_at=created,
        ))
        await session.commit()
    return {
        "protocol_version": "oacp/0.1",
        "id": str(intent_id),
        "status": "pending",
        "merchant_id": body.merchant_id,
        "merchant_name": body.merchant_manifest.merchant_name,
        "order_id": body.order_id,
        "amount_minor": body.amount_minor,
        "currency": body.currency.lower(),
        "merchant_category": body.merchant_category,
        "lines": [line.model_dump() for line in body.lines],
        "expires_at": expires.isoformat(),
        "signing_payload": canonical_json(payload),
    }


async def approve_cart_intent(user_id: str, intent_id: UUID, signature_b64: str) -> dict:
    signature = decode_signature(signature_b64)
    settings = get_settings()
    async with Session() as session:
        intent = await session.scalar(select(PurchaseIntent).where(PurchaseIntent.id == intent_id, PurchaseIntent.user_id == user_id).with_for_update())
        key = await session.get(ApprovalKey, user_id)
        if not intent or not key:
            raise LookupError("Purchase intent not found")
        if intent.status == "approved" and intent.token_expires_at and intent.token_expires_at > datetime.now(UTC):
            token = _checkout_token(intent.id, user_id, intent.merchant_id, intent.token_expires_at)
            return {"id": str(intent.id), "status": "approved", "checkout_token": token, "expires_at": intent.token_expires_at.isoformat(), "amount_minor": intent.amount_minor, "currency": intent.currency, "merchant_id": intent.merchant_id}
        if intent.status not in {"pending", "issuing"} or intent.created_at + timedelta(minutes=15) <= datetime.now(UTC):
            raise LookupError("Purchase intent is expired or no longer pending")
        payload = intent.cart_payload.get("_signing_payload")
        if not isinstance(payload, dict):
            raise RuntimeError("Purchase signing payload is missing")
        if not verify_signature(key.public_key, payload, signature):
            raise PermissionError("Purchase approval signature is invalid")
        if not settings.stripe_issuing_cardholder_id:
            raise RuntimeError("STRIPE_ISSUING_CARDHOLDER_ID must be configured")
        intent.status = "issuing"
        await session.commit()
    idempotency_key = f"oacp-cart-{intent_id}"
    form = {
        "cardholder": settings.stripe_issuing_cardholder_id,
        "currency": intent.currency,
        "type": "virtual",
        "spending_controls[allowed_categories][0]": intent.merchant_category,
        "spending_controls[spending_limits][0][amount]": str(intent.amount_minor),
        "spending_controls[spending_limits][0][interval]": "all_time",
        "lifecycle_controls[cancel_after][payment_count]": "1",
        "metadata[nexusrelay_purchase_intent_id]": str(intent_id),
        "metadata[nexusrelay_merchant_id]": intent.merchant_id,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.stripe.com/v1/issuing/cards",
                auth=_stripe_auth(),
                data=form,
                headers={"Idempotency-Key": idempotency_key},
            )
            response.raise_for_status()
            card = response.json()
    except httpx.HTTPError as exc:
        async with Session() as session:
            stored = await session.get(PurchaseIntent, intent_id)
            if stored:
                stored.status = "issuance_failed"
                await session.commit()
        raise RuntimeError("Stripe Issuing could not create the spend-limited card") from exc
    if not isinstance(card.get("id"), str) or card.get("status") != "active":
        async with Session() as session:
            stored = await session.get(PurchaseIntent, intent_id)
            if stored:
                stored.status = "issuance_failed"
                await session.commit()
        raise RuntimeError("Stripe Issuing did not return an active virtual card")
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.commerce_token_ttl_seconds)
    token = _checkout_token(intent_id, user_id, intent.merchant_id, expires_at)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    async with Session() as session:
        stored = await session.get(PurchaseIntent, intent_id)
        if not stored or stored.status != "issuing":
            raise RuntimeError("Purchase intent changed during card issuance")
        stored.stripe_card_id = card["id"]
        stored.token_hash = token_hash
        stored.token_expires_at = expires_at
        stored.status = "approved"
        await session.commit()
    return {"id": str(intent_id), "status": "approved", "checkout_token": token, "expires_at": expires_at.isoformat(), "amount_minor": intent.amount_minor, "currency": intent.currency, "merchant_id": intent.merchant_id}


def seal_checkout_envelope(
    *,
    token_id: UUID,
    merchant_public_key: X25519PublicKey,
    plaintext: dict[str, Any],
    associated_data: dict[str, Any],
) -> dict[str, str]:
    """Encrypt card data to the merchant key: X25519, HKDF-SHA256, AES-256-GCM."""
    aad = canonical_json(associated_data).encode()
    ephemeral = X25519PrivateKey.generate()
    shared = ephemeral.exchange(merchant_public_key)
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=token_id.bytes, info=b"nexusrelay-oacp-0.1-checkout").derive(shared)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, json.dumps(plaintext, separators=(",", ":")).encode(), aad)
    return {
        "ephemeral_public_key": _b64url(ephemeral.public_key().public_bytes_raw()),
        "nonce": _b64url(nonce),
        "ciphertext": _b64url(ciphertext),
        "associated_data": aad.decode(),
    }


async def exchange_checkout_token(request: CheckoutExchangeRequest, merchant_secret: str | None) -> dict:
    if not merchant_secret:
        raise PermissionError("Merchant authentication failed")
    try:
        merchant_public = X25519PublicKey.from_public_bytes(_decode_b64url(request.merchant_x25519_public_key))
    except (ValueError, TypeError) as exc:
        raise ValueError("merchant_x25519_public_key must encode a 32-byte X25519 public key") from exc
    token_hash = hashlib.sha256(request.checkout_token.encode()).hexdigest()
    exchange_request_hash = hashlib.sha256(canonical_json({
        "merchant_id": request.merchant_id,
        "merchant_x25519_public_key": request.merchant_x25519_public_key,
    }).encode()).hexdigest()
    async with Session() as session:
        trust = await session.get(MerchantCredential, request.merchant_id)
        supplied_secret_hash = hashlib.sha256(merchant_secret.encode()).hexdigest()
        if not trust or not trust.enabled or not hmac.compare_digest(trust.secret_hash, supplied_secret_hash):
            raise PermissionError("Merchant authentication failed")
        intent = await session.scalar(select(PurchaseIntent).where(PurchaseIntent.token_hash == token_hash).with_for_update())
        if not intent or intent.merchant_id != request.merchant_id:
            raise LookupError("Checkout token is invalid, consumed, or bound to a different merchant")
        if intent.status == "exchanged":
            if intent.exchange_idempotency_key == request.idempotency_key and intent.exchange_request_hash == exchange_request_hash and intent.exchange_result:
                return intent.exchange_result
            raise LookupError("Checkout token has already been exchanged")
        if intent.status not in {"approved", "exchanging"}:
            raise LookupError("Checkout token is invalid or consumed")
        if not intent.token_expires_at or intent.token_expires_at <= datetime.now(UTC):
            intent.status = "expired_pending_cancel"
            card_id = intent.stripe_card_id
            await session.commit()
            if card_id:
                await cancel_issuing_card(card_id)
            raise LookupError("Checkout token has expired")
        if intent.status == "exchanging":
            if intent.exchange_idempotency_key != request.idempotency_key or intent.exchange_request_hash != exchange_request_hash:
                raise LookupError("Checkout exchange is already in progress with a different idempotency key")
            if intent.exchange_started_at and intent.exchange_started_at > datetime.now(UTC) - timedelta(seconds=30):
                raise LookupError("Checkout exchange is already in progress; retry with the same idempotency key")
            intent.exchange_started_at = datetime.now(UTC)
        else:
            intent.status = "exchanging"
            intent.exchange_idempotency_key = request.idempotency_key
            intent.exchange_request_hash = exchange_request_hash
            intent.exchange_started_at = datetime.now(UTC)
        await session.commit()
        card_id = intent.stripe_card_id
        intent_id = intent.id
        expires_at = intent.token_expires_at
        amount_minor = intent.amount_minor
        currency = intent.currency
        order_id = intent.order_id
    if not card_id:
        raise RuntimeError("Approved purchase has no associated Issuing card")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"https://api.stripe.com/v1/issuing/cards/{card_id}",
                params=[("expand[]", "number"), ("expand[]", "cvc")],
                auth=_stripe_auth(),
            )
            response.raise_for_status()
            card_data = response.json()
    except httpx.HTTPError as exc:
        await cancel_issuing_card(card_id)
        async with Session() as session:
            stored = await session.get(PurchaseIntent, intent_id)
            if stored:
                stored.status = "exchange_failed"
                await session.commit()
        raise RuntimeError("Stripe could not provide the card to the merchant exchange") from exc
    pan = card_data.get("number")
    cvc = card_data.get("cvc")
    if not isinstance(pan, str) or not isinstance(cvc, str):
        await cancel_issuing_card(card_id)
        async with Session() as session:
            stored = await session.get(PurchaseIntent, intent_id)
            if stored:
                stored.status = "exchange_failed"
                await session.commit()
        raise RuntimeError("Stripe did not provide retrievable virtual card details")
    token_id = intent_id
    aad_obj = {
        "protocol_version": "oacp/0.1",
        "token_id": str(token_id),
        "merchant_id": request.merchant_id,
        "order_id": order_id,
        "amount_minor": amount_minor,
        "currency": currency,
        "issued_at": datetime.now(UTC).isoformat(),
    }
    sealed = seal_checkout_envelope(
        token_id=token_id,
        merchant_public_key=merchant_public,
        plaintext={
            "pan": pan,
            "cvc": cvc,
            "exp_month": card_data["exp_month"],
            "exp_year": card_data["exp_year"],
            "amount_minor": amount_minor,
            "currency": currency,
            "order_id": order_id,
        },
        associated_data=aad_obj,
    )
    result = {
        "protocol_version": "oacp/0.1",
        "token_id": str(token_id),
        **sealed,
        "expires_at": expires_at.isoformat(),
    }
    async with Session() as session:
        stored = await session.get(PurchaseIntent, intent_id)
        if not stored or stored.status != "exchanging" or stored.exchange_idempotency_key != request.idempotency_key:
            raise RuntimeError("Checkout exchange state changed before completion")
        stored.status = "exchanged"
        stored.exchanged_at = datetime.now(UTC)
        stored.exchange_result = result
        await session.commit()
    return result


async def cancel_issuing_card(card_id: str) -> None:
    settings = get_settings()
    if not settings.stripe_secret_key:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"https://api.stripe.com/v1/issuing/cards/{card_id}",
            auth=_stripe_auth(),
            data={"status": "canceled"},
            headers={"Idempotency-Key": f"nexusrelay-cancel-{card_id}"},
        )
        response.raise_for_status()


async def expire_unused_cards() -> None:
    now = datetime.now(UTC)
    async with Session() as session:
        intents = (await session.scalars(select(PurchaseIntent).where(
            PurchaseIntent.status.in_(["approved", "expired_pending_cancel"]), PurchaseIntent.token_expires_at <= now
        ).limit(100))).all()
        for intent in intents:
            intent.status = "expired_pending_cancel"
        await session.commit()
    for intent in intents:
        if intent.stripe_card_id:
            try:
                await cancel_issuing_card(intent.stripe_card_id)
            except httpx.HTTPError:
                continue
            async with Session() as session:
                stored = await session.get(PurchaseIntent, intent.id)
                if stored and stored.status == "expired_pending_cancel":
                    stored.status = "expired"
                    await session.commit()

