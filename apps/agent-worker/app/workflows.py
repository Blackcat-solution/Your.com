from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


@dataclass
class RelayInput:
    run_id: str
    user_id: str
    task: str
    repository: str
    base_branch: str
    slack_channel_id: str


@dataclass
class ActivityResult:
    next_node: str | None
    summary: dict[str, Any]


@dataclass
class ApprovalSignal:
    approval_id: str
    decision: str


@workflow.defn
class RelayFlowWorkflow:
    def __init__(self) -> None:
        self.decisions: dict[str, str] = {}

    @workflow.signal
    def approval_decided(self, signal: ApprovalSignal) -> None:
        self.decisions[signal.approval_id] = signal.decision

    @workflow.run
    async def run(self, request: RelayInput) -> dict[str, Any]:
        common = {
            "start_to_close_timeout": timedelta(hours=3),
            "schedule_to_close_timeout": timedelta(hours=8),
            "retry_policy": RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2), maximum_interval=timedelta(minutes=2)),
        }
        try:
            await workflow.execute_activity("set_run_status_activity", (request.run_id, "running"), **common)
            payload: dict[str, Any] = {
                "run_id": request.run_id,
                "user_id": request.user_id,
                "task": request.task,
                "repository": request.repository,
                "base_branch": request.base_branch,
                "slack_channel_id": request.slack_channel_id,
            }
            summary = await workflow.execute_activity("invoke_graph_activity", (request.run_id, payload), **common)
            while summary.get("next_node"):
                node = str(summary["next_node"])
                if node not in {"publish_pr", "post_slack"}:
                    raise RuntimeError(f"Unexpected graph breakpoint: {node}")
                action = "github.pull_request.publish" if node == "publish_pr" else "slack.summary.publish"
                approval = await workflow.execute_activity(
                    "create_approval_activity",
                    (request.run_id, request.user_id, action, summary),
                    **common,
                )
                await workflow.execute_activity("notify_frontend_activity", (request.run_id, approval), **common)
                approved = await workflow.wait_condition(
                    lambda: approval["id"] in self.decisions,
                    timeout=timedelta(days=7),
                )
                if not approved:
                    await workflow.execute_activity("set_run_status_activity", (request.run_id, "approval_expired"), **common)
                    return {"status": "approval_expired"}
                decision = self.decisions.pop(approval["id"])
                summary = await workflow.execute_activity("resume_graph_activity", (request.run_id, decision), **common)
            status = "cancelled" if summary.get("cancelled") else "completed"
            await workflow.execute_activity("set_run_status_activity", (request.run_id, status), **common)
            await workflow.execute_activity(
                "emit_run_event_activity",
                (request.run_id, f"{request.run_id}:terminal", "run_completed", {"status": status, "outcome": summary.get("outcome")}),
                **common,
            )
            return {"status": status, "outcome": summary.get("outcome"), "pull_request_url": summary.get("pull_request_url")}
        except Exception:
            await workflow.execute_activity("set_run_status_activity", (request.run_id, "failed"), **common)
            await workflow.execute_activity(
                "emit_run_event_activity",
                (request.run_id, f"{request.run_id}:terminal", "run_failed", {"status": "failed", "message": "Worker failed; inspect protected worker logs."}),
                **common,
            )
            return {"status": "failed"}

