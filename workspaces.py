from uuid import UUID

from sqlalchemy import select

from app.connectors.mcp_gateway import MCPGateway
from app.db import ApprovalRequest, Session
from app.settings import get_settings


async def _require_approval(user_id: str, approval_id: UUID, action: str) -> None:
    async with Session() as session:
        approval = await session.scalar(select(ApprovalRequest).where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.user_id == user_id,
            ApprovalRequest.action == action,
            ApprovalRequest.status == "decided",
            ApprovalRequest.decision == "approve",
        ))
        if not approval:
            raise PermissionError(f"A signed approval for {action} is required")


class GoogleDriveConnector:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.gateway = MCPGateway(self.settings)

    async def search(self, user_id: str, query: str, limit: int = 20) -> dict:
        return await self.gateway.call("google_drive", self.settings.mcp_drive_search_tool, {"query": query, "limit": min(max(limit, 1), 100)}, user_id=user_id)

    async def read_file(self, user_id: str, file_id: str) -> dict:
        return await self.gateway.call("google_drive", self.settings.mcp_drive_get_file_tool, {"file_id": file_id}, user_id=user_id)

    async def create_document(self, user_id: str, approval_id: UUID, title: str, content: str) -> dict:
        await _require_approval(user_id, approval_id, "google_drive.document.publish")
        return await self.gateway.call("google_drive", self.settings.mcp_drive_create_file_tool, {"name": title, "mime_type": "text/markdown", "content": content}, user_id=user_id)


class MicrosoftGraphConnector:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.gateway = MCPGateway(self.settings)

    async def search_mail(self, user_id: str, query: str, limit: int = 20) -> dict:
        return await self.gateway.call("microsoft_graph", self.settings.mcp_graph_search_mail_tool, {"query": query, "limit": min(max(limit, 1), 100)}, user_id=user_id)

    async def create_document(self, user_id: str, approval_id: UUID, title: str, content: str) -> dict:
        await _require_approval(user_id, approval_id, "microsoft_graph.document.publish")
        return await self.gateway.call("microsoft_graph", self.settings.mcp_graph_create_document_tool, {"title": title, "content": content}, user_id=user_id)

    async def send_mail(self, user_id: str, approval_id: UUID, to: list[str], subject: str, body: str) -> dict:
        await _require_approval(user_id, approval_id, "microsoft_graph.email.send")
        return await self.gateway.call("microsoft_graph", self.settings.mcp_graph_send_mail_tool, {"to": to, "subject": subject, "body": body}, user_id=user_id)

