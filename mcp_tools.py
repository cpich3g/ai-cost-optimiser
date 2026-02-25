"""MCP scaffold helpers.

This project keeps orchestration logic in src/ and exposes no-op MCP hooks here.
"""

from typing import Any


def is_mcp_enabled() -> bool:
    """Return whether MCP tools are enabled via config."""
    from config import ENABLE_MCP_TOOLS

    return ENABLE_MCP_TOOLS


def call_tool(tool_name: str, **kwargs: Any) -> dict[str, Any]:
    """Placeholder MCP tool call wrapper."""
    return {
        "enabled": is_mcp_enabled(),
        "tool": tool_name,
        "args": kwargs,
        "status": "not_implemented",
    }
