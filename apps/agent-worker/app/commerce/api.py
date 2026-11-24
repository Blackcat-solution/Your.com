import base64
import binascii
import hashlib
import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException

from app.auth import current_user
from app.commerce.service import approve_cart_intent, create_cart_intent, exchange_checkout_token
from app.db import MerchantCredential, Session
from app.schemas import (
    CartIntentCreate,
    CheckoutExchangeRequest,
    CheckoutExchangeView,
    MerchantTrustWrite,
    PurchaseApproval,
)
from app.settings import get_settings

router = APIRouter(prefix="/v1/commerce", tags=["OACP"])


@router.post("/cart-intents", status_code=201)
async def post_cart_intent(body: CartIntentCreate, user_id: str = Depends(current_user)) -> dict:
    try:
        return await create_cart_intent(user_id, body)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/cart-intents/{intent_id}/approve")
async def post_purchase_approval(intent_id: UUID, body: PurchaseApproval, user_id: str = Depends(current_user)) -> dict:
    try:
        return await approve_cart_intent(user_id, intent_id, body.signature)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/checkout-token/exchange", response_model=CheckoutExchangeView)
async def post_checkout_token_exchange(
    body: CheckoutExchangeRequest,
    x_merchant_token: str | None = Header(default=None),
) -> CheckoutExchangeView:
    try:
        return CheckoutExchangeView(**await exchange_checkout_token(body, x_merchant_token))
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/merchants/{merchant_id}/trust", status_code=201)
async def register_merchant_trust(
    merchant_id: str,
    body: MerchantTrustWrite,
    x_oacp_admin_token: str | None = Header(default=None),
) -> dict[str, str]:
    admin_token = get_settings().oacp_admin_token
    if not admin_token or len(admin_token) < 32 or not x_oacp_admin_token or not hmac.compare_digest(admin_token, x_oacp_admin_token):
        raise HTTPException(status_code=401, detail="OACP administrator authentication failed")
    try:
        public_key = base64.urlsafe_b64decode(body.signing_public_key + "=" * (-len(body.signing_public_key) % 4))
        if len(public_key) != 32 or not str(body.origin).startswith("https://"):
            raise ValueError("Invalid public key or origin")
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=422, detail="Expected a base64url Ed25519 public key and HTTPS origin") from exc
    fingerprint = hashlib.sha256(public_key).hexdigest()
    async with Session() as session:
        row = await session.get(MerchantCredential, merchant_id)
        if row is None:
            session.add(MerchantCredential(
                merchant_id=merchant_id,
                origin=str(body.origin).rstrip("/"),
                signing_key_id=body.signing_key_id,
                signing_public_key=public_key,
                secret_hash=hashlib.sha256(body.exchange_secret.encode()).hexdigest(),
                enabled=True,
            ))
        else:
            row.origin = str(body.origin).rstrip("/")
            row.signing_key_id = body.signing_key_id
            row.signing_public_key = public_key
            row.secret_hash = hashlib.sha256(body.exchange_secret.encode()).hexdigest()
            row.enabled = True
        await session.commit()
    return {"status": "registered", "merchant_id": merchant_id, "signing_key_id": body.signing_key_id, "signing_key_sha256": fingerprint}
