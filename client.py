from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class StreamableMCPClient:
    """A minimal MCP client that opens a fresh authenticated HTTP session per call."""

    async def call(
        self,
        url: str,
        bearer_token: str | None,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else None
        async with streamable_http_client(url, headers=headers) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
        if getattr(result, "isError", False):
            message = " ".join(getattr(item, "text", "") for item in result.content)
            raise RuntimeError(f"MCP tool '{tool_name}' failed: {message[:500]}")
        structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
        if isinstance(structured, dict):
            return structured
        parts = [getattr(item, "text", "") for item in getattr(result, "content", []) if getattr(item, "text", "")]
        return {"content": "\n".join(parts)}

