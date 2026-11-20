import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis

from app.auth import current_user
from app.settings import get_settings
from app.token_vault import OAuthTokenVault

router = APIRouter(prefix="/v1/integrations", tags=["OAuth integrations"])


def _provider(provider: str) -> dict[str, str]:
    settings = get_settings()
    configs = {
        "github": {
            "client_id": settings.oauth_github_client_id,
            "client_secret": settings.oauth_github_client_secret,
            "authorize_url": settings.oauth_github_authorize_url,
            "token_url": settings.oauth_github_token_url,
            "redirect_uri": settings.oauth_github_redirect_uri,
            "scope": "repo read:user",
            "pkce": "true",
        },
        "slack": {
            "client_id": settings.oauth_slack_client_id,
            "client_secret": settings.oauth_slack_client_secret,
            "authorize_url": settings.oauth_slack_authorize_url,
            "token_url": settings.oauth_slack_token_url,
            "redirect_uri": settings.oauth_slack_redirect_uri,
            "scope": "chat:write channels:read",
            "pkce": "false",
        },
        "google_drive": {
            "client_id": settings.oauth_google_client_id,
            "client_secret": settings.oauth_google_client_secret,
            "authorize_url": settings.oauth_google_authorize_url,
            "token_url": settings.oauth_google_token_url,
            "redirect_uri": settings.oauth_google_redirect_uri,
            "scope": "openid email https://www.googleapis.com/auth/drive.file",
            "pkce": "true",
        },
        "microsoft_graph": {
            "client_id": settings.oauth_microsoft_client_id,
            "client_secret": settings.oauth_microsoft_client_secret,
            "authorize_url": settings.oauth_microsoft_authorize_url,
            "token_url": settings.oauth_microsoft_token_url,
            "redirect_uri": settings.oauth_microsoft_redirect_uri,
            "scope": "openid profile offline_access User.Read Files.ReadWrite Mail.Read Mail.Send",
            "pkce": "true",
        },
    }
    config = configs.get(provider)
    if not config or not all(config.get(key) for key in ("client_id", "client_secret", "redirect_uri")):
        raise HTTPException(status_code=503, detail=f"OAuth client is not configured for {provider}")
    return config


async def _redis(request: Request) -> Redis:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
        request.app.state.redis = redis
    return redis


@router.get("/{provider}/oauth/start")
async def oauth_start(provider: str, request: Request, user_id: str = Depends(current_user)) -> dict[str, str]:
    config = _provider(provider)
    try:
        OAuthTokenVault()._cipher()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Token vault encryption is not configured") from exc
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    await (await _redis(request)).set(
        f"nexusrelay:oauth-state:{state}",
        json.dumps({"user_id": user_id, "provider": provider, "verifier": verifier}, separators=(",", ":")),
        ex=600,
        nx=True,
    )
    params = {
        "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"],
        "response_type": "code",
        "scope": config["scope"],
        "state": state,
    }
    if config["pkce"] == "true":
        params.update({"code_challenge": challenge, "code_challenge_method": "S256"})
    if provider == "google_drive":
        params.update({"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"})
    if provider == "slack":
        params["user_scope"] = params.pop("scope")
    return {"authorization_url": f"{config['authorize_url']}?{urlencode(params)}", "expires_in_seconds": "600"}


@router.get("/{provider}/oauth/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    code: str | None = Query(default=None, max_length=4096),
    state: str = Query(min_length=20, max_length=200),
    error: str | None = Query(default=None, max_length=128),
) -> RedirectResponse:
    if error or not code:
        raise HTTPException(status_code=400, detail="OAuth authorization was declined or returned no code")
    config = _provider(provider)
    redis = await _redis(request)
    state_json = await redis.getdel(f"nexusrelay:oauth-state:{state}")
    if not state_json:
        raise HTTPException(status_code=400, detail="OAuth state is invalid, expired, or already used")
    state_data = json.loads(state_json)
    if state_data.get("provider") != provider:
        raise HTTPException(status_code=400, detail="OAuth state provider mismatch")
    form = {
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "code": code,
        "redirect_uri": config["redirect_uri"],
    }
    if config["pkce"] == "true":
        form["code_verifier"] = state_data["verifier"]
    if provider == "microsoft_graph":
        form.update({"grant_type": "authorization_code", "scope": config["scope"]})
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(config["token_url"], data=form, headers={"Accept": "application/json"})
            response.raise_for_status()
            result = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="OAuth token exchange failed") from exc
    token_result = result.get("authed_user", result) if provider == "slack" else result
    access_token = token_result.get("access_token")
    refresh_token = token_result.get("refresh_token")
    if not isinstance(access_token, str) or not access_token:
        raise HTTPException(status_code=502, detail="OAuth provider returned no access token")
    expires_in = token_result.get("expires_in")
    expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in)) if expires_in else None
    scopes = token_result.get("scope", "")
    if isinstance(scopes, str):
        scopes = scopes.replace(",", " ").split()
    if not isinstance(scopes, list):
        scopes = config["scope"].split()
    await OAuthTokenVault().store(state_data["user_id"], provider, access_token, refresh_token, expires_at, scopes)
    target = f"{get_settings().frontend_public_url.rstrip('/')}/?integration=connected&provider={provider}"
    return RedirectResponse(target, status_code=303)

