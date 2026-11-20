import asyncio
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from sqlalchemy import select
from temporalio import activity

from app.db import ApprovalRequest, Run, RunEvent, Session, append_event
from app.graph import invoke_graph
from app.settings import get_settings


async def _heartbeat_loop() -> None:
    while True:
        activity.heartbeat()
        await asyncio.sleep(20)


async def _run_with_heartbeat(awaitable: Any) -> Any:
    heartbeat = asyncio.create_task(_heartbeat_loop())
    try:
        return await awaitable
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass


@activity.defn
async def invoke_graph_activity(args: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    run_id, payload = args
    return await _run_with_heartbeat(invoke_graph(run_id, payload))


@activity.defn
async def resume_graph_activity(args: tuple[str, str]) -> dict[str, Any]:
    run_id, decision = args
    return await _run_with_heartbeat(invoke_graph(run_id, {}, resume_decision=decision))


@activity.defn
async def create_approval_activity(args: tuple[str, str, str, dict[str, Any]]) -> dict[str, Any]:
    run_id_text, user_id, action, graph_summary = args
    run_id = UUID(run_id_text)
    action_key = f"{run_id}:{action}"
    approval_id = uuid5(NAMESPACE_URL, action_key)
    async with Session() as session:
        existing = await session.scalar(select(ApprovalRequest).where(ApprovalRequest.id == approval_id))
        if existing:
            return {
                "id": str(existing.id),
                "run_id": run_id_text,
                "action": existing.action,
                "summary": existing.summary,
                "expires_at": existing.expires_at.isoformat(),
                "signing_payload": json.dumps(existing.signing_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            }
        expires_at = datetime.now(UTC) + timedelta(days=7)
        if action == "github.pull_request.publish":
            summary = {
                "title": "Create a draft pull request with the researched specification",
                "repository": graph_summary.get("repository"),
                "branch": graph_summary.get("branch"),
                "task": graph_summary.get("task"),
                "spec_preview": graph_summary.get("spec_excerpt", ""),
                "external_effect": "Creates a branch, commits the specification, and opens a draft pull request.",
            }
        else:
            summary = {
                "title": "Post the completion summary to Slack",
                "channel": graph_summary.get("slack_channel_id"),
                "task": graph_summary.get("task"),
                "pull_request_url": graph_summary.get("pull_request_url"),
                "research_preview": graph_summary.get("research_excerpt", ""),
                "external_effect": "Sends a message to the configured Slack channel.",
            }
        summary_hash = hashlib.sha256(json.dumps(summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        signing_payload = {
            "approval_id": str(approval_id),
            "run_id": run_id_text,
            "user_id": user_id,
            "action": action,
            "summary_sha256": summary_hash,
            "nonce": secrets.token_urlsafe(32),
            "expires_at": expires_at.isoformat(),
        }
        request = ApprovalRequest(
            id=approval_id,
            run_id=run_id,
            user_id=user_id,
            action_key=action_key,
            action=action,
            summary=summary,
            signing_payload=signing_payload,
            expires_at=expires_at,
            status="pending",
        )
        session.add(request)
        run = await session.get(Run, run_id)
        if run:
            run.status = "waiting_approval"
            run.updated_at = datetime.now(UTC)
        await append_event(
            session,
            run_id,
            f"approval:{approval_id}",
            "approval_required",
            {"approval_id": str(approval_id), "action": action, "summary": summary, "expires_at": expires_at.isoformat()},
        )
        await session.commit()
    return {
        "id": str(approval_id),
        "run_id": run_id_text,
        "action": action,
        "summary": summary,
        "expires_at": expires_at.isoformat(),
        "signing_payload": json.dumps(signing_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
    }


@activity.defn
async def notify_frontend_activity(args: tuple[str, dict[str, Any]]) -> None:
    run_id, approval = args
    settings = get_settings()
    if not settings.frontend_webhook_url:
        return
    if len(settings.frontend_webhook_secret.encode()) < 32:
        raise RuntimeError("FRONTEND_WEBHOOK_SECRET must contain at least 32 bytes")
    payload = json.dumps(
        {"event": "approval_required", "run_id": run_id, "approval_id": approval["id"], "action": approval["action"]},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(settings.frontend_webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            settings.frontend_webhook_url,
            content=payload,
            headers={"Content-Type": "application/json", "X-Nexus-Signature": f"sha256={signature}"},
        )
        response.raise_for_status()


@activity.defn
async def emit_run_event_activity(args: tuple[str, str, str, dict[str, Any]]) -> None:
    run_id_text, event_key, event_type, data = args
    async with Session() as session:
        await append_event(session, UUID(run_id_text), event_key, event_type, data)
        await session.commit()


@activity.defn
async def set_run_status_activity(args: tuple[str, str]) -> None:
    run_id_text, status = args
    async with Session() as session:
        run = await session.get(Run, UUID(run_id_text))
        if run:
            run.status = status
            run.updated_at = datetime.now(UTC)
            await append_event(
                session,
                run.id,
                f"{run.id}:status:{status}",
                "run_status",
                {"status": status},
            )
            await session.commit()

