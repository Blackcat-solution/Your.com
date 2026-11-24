from dataclasses import dataclass, field
from typing import Any

from nexusrelay_mcp import StreamableMCPClient

from app.settings import Settings, get_settings
from app.token_vault import OAuthTokenVault


@dataclass(frozen=True)
class MCPServer:
    url: str
    bearer_token: str | None = field(repr=False)


class MCPGateway:
    """Short-lived Streamable HTTP sessions; access tokens never enter graph state."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = StreamableMCPClient()

    def server(self, name: str) -> MCPServer:
        values = {
            "github": MCPServer(self.settings.mcp_github_url, self.settings.mcp_github_bearer_token),
            "slack": MCPServer(self.settings.mcp_slack_url, self.settings.mcp_slack_bearer_token),
            "google_drive": MCPServer(self.settings.mcp_google_drive_url, None),
            "microsoft_graph": MCPServer(self.settings.mcp_microsoft_graph_url, None),
        }
        server = values.get(name)
        if server is None or not server.url:
            raise RuntimeError(f"MCP server '{name}' is not configured")
        return server

    async def call(self, server_name: str, tool_name: str, arguments: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
        server = self.server(server_name)
        token = server.bearer_token
        vaulted = False
        if user_id and server_name in {"github", "slack", "google_drive", "microsoft_graph"}:
            try:
                token = await OAuthTokenVault(self.settings).get_access_token(user_id, server_name)
                vaulted = True
            except RuntimeError:
                if self.settings.app_env != "development":
                    raise
        if self.settings.app_env == "production" and not vaulted:
            raise RuntimeError("Production MCP calls must use a per-user vaulted OAuth token")
        return await self.client.call(server.url, token, tool_name, arguments)

