import asyncio
from functools import lru_cache

import httpx
import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

from app.settings import get_settings


@lru_cache(maxsize=8)
def _jwks_client(issuer: str) -> PyJWKClient:
    discovery_url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    response = httpx.get(discovery_url, timeout=10)
    response.raise_for_status()
    jwks_uri = response.json().get("jwks_uri")
    if not isinstance(jwks_uri, str) or not jwks_uri.startswith("https://"):
        raise ValueError("OIDC discovery document has no HTTPS jwks_uri")
    return PyJWKClient(jwks_uri, cache_keys=True)


async def current_user(authorization: str | None = Header(default=None), x_user_id: str | None = Header(default=None)) -> str:
    settings = get_settings()
    if settings.auth_mode == "dev" and settings.app_env == "development":
        return (x_user_id or "local-dev-user")[:255]
    if settings.auth_mode != "oidc" or not settings.oidc_issuer or not settings.oidc_audience:
        raise HTTPException(status_code=503, detail="OIDC authentication is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        key = await asyncio.to_thread(_jwks_client(settings.oidc_issuer).get_signing_key_from_jwt, token)
        claims = jwt.decode(token, key.key, algorithms=["RS256", "ES256"], issuer=settings.oidc_issuer, audience=settings.oidc_audience)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid access token") from exc
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(status_code=401, detail="Token has no subject")
    return subject[:255]

