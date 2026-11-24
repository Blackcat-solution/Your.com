import base64
import binascii
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from app.db import OAuthCredential, Session
from app.settings import Settings, get_settings


class OAuthTokenVault:
    """Encrypts user OAuth access/refresh tokens before writing them to PostgreSQL."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _cipher(self) -> AESGCM:
        try:
            key = base64.b64decode(self.settings.token_vault_key_b64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise RuntimeError("TOKEN_VAULT_KEY_B64 must be a base64-encoded 32-byte key") from exc
        if len(key) != 32:
            raise RuntimeError("TOKEN_VAULT_KEY_B64 must decode to exactly 32 bytes")
        return AESGCM(key)

    @staticmethod
    def _aad(user_id: str, provider: str) -> bytes:
        return f"nexusrelay-oauth-v1:{user_id}:{provider}".encode()

    def seal(self, user_id: str, provider: str, payload: dict[str, Any]) -> tuple[bytes, bytes]:
        """Encrypt a token payload; the AAD binds the ciphertext to its owner."""
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher().encrypt(
            nonce,
            json.dumps(payload, separators=(",", ":")).encode(),
            self._aad(user_id, provider),
        )
        return nonce, ciphertext

    def unseal(self, user_id: str, provider: str, nonce: bytes, ciphertext: bytes) -> dict[str, Any]:
        payload: dict[str, Any] = json.loads(
            self._cipher().decrypt(nonce, ciphertext, self._aad(user_id, provider))
        )
        return payload

    async def store(
        self,
        user_id: str,
        provider: str,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime | None,
        scopes: list[str],
    ) -> None:
        if provider not in {"github", "slack", "google_drive", "microsoft_graph"}:
            raise ValueError("Unsupported OAuth provider")
        if not access_token or len(access_token) > 16_384:
            raise ValueError("Access token is empty or too long")
        nonce, ciphertext = self.seal(
            user_id,
            provider,
            {"access_token": access_token, "refresh_token": refresh_token},
        )
        async with Session() as session:
            row = await session.scalar(
                select(OAuthCredential).where(OAuthCredential.user_id == user_id, OAuthCredential.provider == provider)
            )
            if row is None:
                row = OAuthCredential(user_id=user_id, provider=provider, nonce=nonce, ciphertext=ciphertext)
                session.add(row)
            row.nonce = nonce
            row.ciphertext = ciphertext
            row.expires_at = expires_at
            row.scopes = scopes
            row.updated_at = datetime.now(UTC)
            await session.commit()

    def _provider_config(self, provider: str) -> tuple[str, str, str]:
        prefix = {
            "github": (self.settings.oauth_github_token_url, self.settings.oauth_github_client_id, self.settings.oauth_github_client_secret),
            "slack": (self.settings.oauth_slack_token_url, self.settings.oauth_slack_client_id, self.settings.oauth_slack_client_secret),
            "google_drive": (self.settings.oauth_google_token_url, self.settings.oauth_google_client_id, self.settings.oauth_google_client_secret),
            "microsoft_graph": (self.settings.oauth_microsoft_token_url, self.settings.oauth_microsoft_client_id, self.settings.oauth_microsoft_client_secret),
        }
        return prefix[provider]

    async def get_access_token(self, user_id: str, provider: str) -> str:
        async with Session() as session:
            row = await session.scalar(
                select(OAuthCredential).where(OAuthCredential.user_id == user_id, OAuthCredential.provider == provider)
            )
            if row is None:
                raise RuntimeError(f"No vaulted OAuth credential for {provider}")
            payload = self.unseal(user_id, provider, row.nonce, row.ciphertext)
            if row.expires_at is None or row.expires_at > datetime.now(UTC) + timedelta(minutes=2):
                return payload["access_token"]
            refresh_token = payload.get("refresh_token")
            if not refresh_token:
                raise RuntimeError(f"The {provider} OAuth token expired and has no refresh token")
            token_url, client_id, client_secret = self._provider_config(provider)
            if not client_id or not client_secret:
                raise RuntimeError(f"OAuth refresh is not configured for {provider}")
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    token_url,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                refreshed = response.json()
            new_access = refreshed.get("access_token")
            if not isinstance(new_access, str) or not new_access:
                raise RuntimeError(f"OAuth refresh did not return an access token for {provider}")
            new_refresh = refreshed.get("refresh_token") or refresh_token
            expires_in = int(refreshed.get("expires_in", 3600))
            await self.store(user_id, provider, new_access, new_refresh, datetime.now(UTC) + timedelta(seconds=expires_in), row.scopes)
            return new_access

