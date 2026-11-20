import asyncio
import base64
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import AsyncIterator
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError
from temporalio.client import WorkflowHandle

from app.auth import current_user
from app.commerce.api import router as commerce_router
from app.db import ApprovalKey, ApprovalRequest, Run, RunEvent, Session, append_event, engine, init_db
from app.oauth import router as oauth_router
from app.schemas import ApprovalDecision, ApprovalKeyCreate, ApprovalView, OAuthTokenWrite, RunCreate, RunView
from app.settings import get_settings
from app.token_vault import OAuthTokenVault
from app.workflows import ApprovalSignal, RelayFlowWorkflow, RelayInput


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.app_env == "production":
        if settings.auth_mode != "oidc" or not settings.oidc_issuer or not settings.oidc_audience:
            raise RuntimeError("Production requires AUTH_MODE=oidc, OIDC_ISSUER, and OIDC_AUDIENCE")
        OAuthTokenVault(settings)._cipher()
        if settings.frontend_webhook_url and len(settings.frontend_webhook_secret.encode()) < 32:
            raise RuntimeError("Configured frontend webhook requires a 32-byte signing secret")
    await init_db()
    app.state.temporal = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    try:
        yield
    finally:
        redis = getattr(app.state, "redis", None)
        if redis is not None:
            await redis.aclose()
        await engine.dispose()


app = FastAPI(title="NexusRelay API", version="0.1.0", lifespan=lifespan)
app.include_router(commerce_router)
app.include_router(oauth_router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.put("/v1/integrations/{provider}/oauth-token")
async def store_oauth_token(provider: str, body: OAuthTokenWrite, user_id: str = Depends(current_user)) -> dict[str, str | None]:
    if provider not in {"github", "slack", "google_drive", "microsoft_graph"}:
        raise HTTPException(status_code=404, detail="Unsupported integration provider")
    expires_at = datetime.now(UTC) + timedelta(seconds=body.expires_in_seconds) if body.expires_in_seconds else None
    try:
        await OAuthTokenVault().store(user_id, provider, body.access_token, body.refresh_token, expires_at, body.scopes)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "stored", "provider": provider, "expires_at": expires_at.isoformat() if expires_at else None}


async def _rate_limit(request: Request, user_id: str, label: str, limit: int = 30) -> None:
    """Fixed-window limit backed by Redis; keys include user identity, never credentials."""
    from redis.asyncio import Redis

    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
        request.app.state.redis = redis
    key = f"nexusrelay:rate:{label}:{user_id}:{int(datetime.now(UTC).timestamp()) // 60}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 70)
    if count > limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


@app.post("/v1/approval-keys", status_code=201)
async def register_approval_key(body: ApprovalKeyCreate, user_id: str = Depends(current_user)) -> dict[str, str]:
    try:
        raw_key = base64.b64decode(body.public_key, validate=True)
        Ed25519PublicKey.from_public_bytes(raw_key)
    except (ValueError, base64.binascii.Error) as exc:
        raise HTTPException(status_code=422, detail="Expected a base64-encoded Ed25519 public key") from exc
    fingerprint = hashlib.sha256(raw_key).hexdigest()
    async with Session() as session:
        existing = await session.get(ApprovalKey, user_id)
        if existing:
            if existing.key_fingerprint == fingerprint:
                return {"status": "registered", "fingerprint": fingerprint}
            raise HTTPException(status_code=409, detail="An approval key is already registered for this account")
        session.add(ApprovalKey(user_id=user_id, public_key=raw_key, key_fingerprint=fingerprint))
        await session.commit()
    return {"status": "registered", "fingerprint": fingerprint}


@app.post("/v1/runs", response_model=RunView, status_code=202)
async def create_run(body: RunCreate, request: Request, user_id: str = Depends(current_user)) -> RunView:
    await _rate_limit(request, user_id, "create-run")
    settings = get_settings()
    repository = body.repository or settings.github_repository
    channel = body.slack_channel_id or settings.slack_channel_id
    if not repository or "/" not in repository:
        raise HTTPException(status_code=422, detail="Set a valid repository or GITHUB_REPOSITORY")
    if not channel:
        raise HTTPException(status_code=422, detail="Set slack_channel_id or SLACK_CHANNEL_ID")
    async with Session() as session:
        key = await session.get(ApprovalKey, user_id)
        if key is None:
            raise HTTPException(status_code=409, detail="Register a cryptographic approval key before starting a run")
        run_id = uuid4()
        run = Run(
            id=run_id,
            user_id=user_id,
            task=body.task,
            repository=repository,
            base_branch=body.base_branch or settings.github_base_branch,
            slack_channel_id=channel,
            status="queued",
        )
        session.add(run)
        await append_event(session, run_id, f"{run_id}:status:queued", "run_status", {"status": "queued"})
        await session.commit()
    workflow_input = RelayInput(
        run_id=str(run_id),
        user_id=user_id,
        task=body.task,
        repository=repository,
        base_branch=body.base_branch or settings.github_base_branch,
        slack_channel_id=channel,
    )
    try:
        await request.app.state.temporal.start_workflow(
            RelayFlowWorkflow.run,
            workflow_input,
            id=str(run_id),
            task_queue=settings.temporal_task_queue,
        )
    except WorkflowAlreadyStartedError:
        pass
    except Exception as exc:
        async with Session() as session:
            stored = await session.get(Run, run_id)
            if stored:
                stored.status = "failed"
                stored.updated_at = datetime.now(UTC)
                await append_event(session, run_id, f"{run_id}:workflow-start-failed", "run_failed", {"status": "failed", "message": "Durable worker could not start."})
                await session.commit()
        raise HTTPException(status_code=503, detail="Durable worker is unavailable") from exc
    return RunView(id=run_id, status="queued", created_at=run.created_at)


@app.get("/v1/runs/{run_id}")
async def get_run(run_id: UUID, user_id: str = Depends(current_user)) -> dict:
    async with Session() as session:
        run = await session.scalar(select(Run).where(Run.id == run_id, Run.user_id == user_id))
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return {"id": str(run.id), "task": run.task, "repository": run.repository, "status": run.status, "created_at": run.created_at.isoformat()}


@app.get("/v1/runs/{run_id}/events")
async def stream_run_events(run_id: UUID, request: Request, user_id: str = Depends(current_user), last_event_id: str | None = Header(default=None, alias="Last-Event-ID")) -> StreamingResponse:
    async with Session() as session:
        exists = await session.scalar(select(Run.id).where(Run.id == run_id, Run.user_id == user_id))
        if not exists:
            raise HTTPException(status_code=404, detail="Run not found")
    try:
        cursor = max(0, int(last_event_id or "0"))
    except ValueError:
        cursor = 0

    async def event_stream() -> AsyncIterator[str]:
        nonlocal cursor
        while not await request.is_disconnected():
            async with Session() as session:
                rows = (await session.scalars(
                    select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.id > cursor).order_by(RunEvent.id).limit(200)
                )).all()
                run = await session.get(Run, run_id)
                terminal = run is None or run.status in {"completed", "cancelled", "failed", "approval_expired"}
            for event in rows:
                cursor = event.id
                data = {"run_id": str(run_id), "created_at": event.created_at.isoformat(), **event.data}
                yield f"id: {event.id}\nevent: {event.event_type}\ndata: {json.dumps(data, separators=(',', ':'), ensure_ascii=False)}\n\n"
            if terminal and not rows:
                break
            yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.get("/v1/approvals/{approval_id}", response_model=ApprovalView)
async def get_approval(approval_id: UUID, user_id: str = Depends(current_user)) -> ApprovalView:
    async with Session() as session:
        approval = await session.scalar(select(ApprovalRequest).where(ApprovalRequest.id == approval_id, ApprovalRequest.user_id == user_id))
        if not approval or approval.status != "pending" or approval.expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=404, detail="Pending approval not found")
        return ApprovalView(
            id=approval.id,
            run_id=approval.run_id,
            action=approval.action,
            summary=approval.summary,
            expires_at=approval.expires_at,
            signing_payload=json.dumps(approval.signing_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )


@app.post("/v1/approvals/{approval_id}/decision")
async def decide_approval(approval_id: UUID, body: ApprovalDecision, request: Request, user_id: str = Depends(current_user)) -> dict[str, str]:
    await _rate_limit(request, user_id, "approval", limit=60)
    try:
        signature = base64.b64decode(body.signature, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise HTTPException(status_code=422, detail="Signature must be base64 encoded") from exc
    async with Session() as session:
        approval = await session.scalar(select(ApprovalRequest).where(ApprovalRequest.id == approval_id, ApprovalRequest.user_id == user_id))
        key = await session.get(ApprovalKey, user_id)
        if not approval or not key:
            raise HTTPException(status_code=404, detail="Approval not found")
        if approval.expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=410, detail="Approval has expired")
        if approval.status == "decided":
            if approval.decision != body.decision:
                raise HTTPException(status_code=409, detail="Approval already has a different decision")
        elif approval.status != "pending":
            raise HTTPException(status_code=409, detail="Approval is no longer pending")
        else:
            signed_payload = {**approval.signing_payload, "decision": body.decision}
            message = json.dumps(signed_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            try:
                Ed25519PublicKey.from_public_bytes(key.public_key).verify(signature, message)
            except InvalidSignature as exc:
                raise HTTPException(status_code=401, detail="Approval signature is invalid") from exc
            approval.status = "decided"
            approval.decision = body.decision
            run_id = approval.run_id
            await append_event(
                session,
                run_id,
                f"approval-decision:{approval.id}",
                "approval_decided",
                {"approval_id": str(approval.id), "decision": body.decision, "action": approval.action},
            )
            run = await session.get(Run, run_id)
            if run:
                run.status = "running"
                run.updated_at = datetime.now(UTC)
            await session.commit()
    try:
        handle: WorkflowHandle = request.app.state.temporal.get_workflow_handle(str(approval.run_id))
        await handle.signal("approval_decided", ApprovalSignal(approval_id=str(approval.id), decision=body.decision))
    except RPCError as exc:
        raise HTTPException(status_code=503, detail="Approval saved; worker signal will need retry") from exc
    return {"status": "accepted", "decision": body.decision}

