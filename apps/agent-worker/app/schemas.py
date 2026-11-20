from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator


class RunCreate(BaseModel):
    task: str = Field(min_length=8, max_length=4000)
    repository: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    base_branch: str | None = Field(default=None, min_length=1, max_length=255)
    slack_channel_id: str | None = Field(default=None, max_length=255)


class RunView(BaseModel):
    id: UUID
    status: str
    created_at: datetime


class ApprovalKeyCreate(BaseModel):
    public_key: str = Field(min_length=40, max_length=60)


class ApprovalView(BaseModel):
    id: UUID
    run_id: UUID
    action: str
    summary: dict
    expires_at: datetime
    signing_payload: str


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    signature: str = Field(min_length=80, max_length=128)


class MerchantManifest(BaseModel):
    protocol_version: Literal["oacp/0.1"]
    merchant_id: str = Field(min_length=1, max_length=255)
    merchant_name: str = Field(min_length=1, max_length=255)
    origin: HttpUrl
    checkout_endpoint: HttpUrl
    accepted_currencies: list[str] = Field(min_length=1)
    mcc_category: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    payment_capabilities: list[str] = Field(min_length=1)
    signing_key_id: str = Field(min_length=1, max_length=128)
    signature: str = Field(min_length=80, max_length=128)

    @field_validator("accepted_currencies")
    @classmethod
    def currencies_are_iso_codes(cls, values: list[str]) -> list[str]:
        if any(len(value) != 3 or not value.isalpha() or value != value.upper() for value in values):
            raise ValueError("Currencies must be uppercase three-letter ISO codes")
        return values


class CartLine(BaseModel):
    sku: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    quantity: int = Field(ge=1, le=100)
    unit_amount_minor: int = Field(ge=0, le=100_000_000)


class CartIntentCreate(BaseModel):
    merchant_id: str = Field(min_length=1, max_length=255)
    order_id: str = Field(min_length=1, max_length=255)
    amount_minor: int = Field(gt=0, le=100_000_000)
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    merchant_category: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    lines: list[CartLine] = Field(min_length=1, max_length=100)
    merchant_manifest: MerchantManifest

    @field_validator("merchant_manifest")
    @classmethod
    def manifest_matches_cart(cls, manifest: MerchantManifest, info):
        data = info.data
        if data.get("merchant_id") and data["merchant_id"] != manifest.merchant_id:
            raise ValueError("merchant_id must match the merchant manifest")
        if data.get("currency") and data["currency"].upper() not in manifest.accepted_currencies:
            raise ValueError("currency is not accepted by this merchant")
        if data.get("merchant_category") and data["merchant_category"] != manifest.mcc_category:
            raise ValueError("merchant category must match the manifest")
        return manifest

    @field_validator("currency")
    @classmethod
    def currency_lowercase(cls, value: str) -> str:
        return value.lower()

    @field_validator("lines")
    @classmethod
    def totals_match(cls, values: list[CartLine], info) -> list[CartLine]:
        total = sum(line.quantity * line.unit_amount_minor for line in values)
        if info.data.get("amount_minor") is not None and total != info.data["amount_minor"]:
            raise ValueError("amount_minor must equal the sum of cart line amounts")
        return values


class CartIntentView(BaseModel):
    id: UUID
    status: str
    amount_minor: int
    currency: str
    merchant_id: str
    order_id: str
    expires_at: datetime
    signing_payload: str


class PurchaseApproval(BaseModel):
    signature: str = Field(min_length=80, max_length=128)


class CheckoutExchangeRequest(BaseModel):
    protocol_version: Literal["oacp/0.1"] = "oacp/0.1"
    checkout_token: str = Field(min_length=40, max_length=100)
    merchant_id: str = Field(min_length=1, max_length=255)
    merchant_x25519_public_key: str = Field(min_length=40, max_length=60)
    idempotency_key: str = Field(min_length=16, max_length=128)


class CheckoutExchangeView(BaseModel):
    protocol_version: Literal["oacp/0.1"]
    token_id: UUID
    ephemeral_public_key: str
    nonce: str
    ciphertext: str
    associated_data: str
    expires_at: datetime


class OAuthTokenWrite(BaseModel):
    access_token: str = Field(min_length=1, max_length=16_384, repr=False)
    refresh_token: str | None = Field(default=None, max_length=16_384, repr=False)
    expires_in_seconds: int | None = Field(default=None, ge=60, le=31_536_000)
    scopes: list[str] = Field(default_factory=list, max_length=100)


class MerchantTrustWrite(BaseModel):
    origin: HttpUrl
    signing_key_id: str = Field(min_length=1, max_length=128)
    signing_public_key: str = Field(min_length=40, max_length=60)
    exchange_secret: str = Field(min_length=32, max_length=256, repr=False)

