# NexusRelay MCP Connectors

This package provides the Streamable HTTP MCP client primitive used by the agent worker. Each tool call creates a short-lived initialized MCP session, sends the bearer token only in the transport header, and returns structured tool results without placing the token in LangGraph state. Configure distinct Slack, GitHub, Google Drive, and Microsoft Graph MCP server URLs. User OAuth credentials are encrypted at rest by the agent worker's token vault.

