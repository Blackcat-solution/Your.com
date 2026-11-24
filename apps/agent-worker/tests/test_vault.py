import base64
import secrets

import pytest
from app.settings import Settings
from app.token_vault import OAuthTokenVault
from cryptography.exceptions import InvalidTag


def _vault(key: bytes | None = None) -> OAuthTokenVault:
    material = key if key is not None else secrets.token_bytes(32)
    return OAuthTokenVault(Settings(token_vault_key_b64=base64.b64encode(material).decode()))


def test_seal_and_unseal_round_trip() -> None:
    vault = _vault()
    nonce, ciphertext = vault.seal("user-1", "github", {"access_token": "tok", "refresh_token": None})
    assert vault.unseal("user-1", "github", nonce, ciphertext)["access_token"] == "tok"


def test_ciphertext_is_bound_to_user_and_provider() -> None:
    vault = _vault()
    nonce, ciphertext = vault.seal("user-1", "github", {"access_token": "tok"})
    with pytest.raises(InvalidTag):
        vault.unseal("user-2", "github", nonce, ciphertext)
    with pytest.raises(InvalidTag):
        vault.unseal("user-1", "slack", nonce, ciphertext)


def test_vault_requires_a_32_byte_key() -> None:
    with pytest.raises(RuntimeError):
        _vault(b"short")._cipher()
    with pytest.raises(RuntimeError):
        OAuthTokenVault(Settings(token_vault_key_b64="not-base64!!"))._cipher()


def test_ciphertext_never_contains_the_token() -> None:
    vault = _vault()
    _, ciphertext = vault.seal("user-1", "github", {"access_token": "super-secret-token"})
    assert b"super-secret-token" not in ciphertext
