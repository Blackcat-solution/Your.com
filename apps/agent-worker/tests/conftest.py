"""Test environment. These values are generated per run and never leave the process."""

import base64
import os
import secrets

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("AUTH_MODE", "dev")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://nexusrelay:nexusrelay@localhost:5432/nexusrelay")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("TOKEN_VAULT_KEY_B64", base64.b64encode(secrets.token_bytes(32)).decode())
os.environ.setdefault("OACP_TOKEN_SIGNING_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("OACP_ADMIN_TOKEN", secrets.token_urlsafe(48))
