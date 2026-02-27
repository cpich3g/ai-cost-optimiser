"""Central configuration for the Azure Cost Optimiser function app."""

import os


def _get_required_env(key: str) -> str:
    """Get required environment variable or raise error if not set."""
    value = os.getenv(key)
    if not value:
        raise ValueError(f"Required environment variable '{key}' is not set")
    return value


# Security-critical settings (must be set)
APPROVAL_CALLBACK_SECRET = _get_required_env("APPROVAL_CALLBACK_SECRET")
SIGNALR_HUB_NAME = os.getenv("SIGNALR_HUB_NAME", "costoptimiser")
SIGNALR_CONNECTION_SETTING = os.getenv("SIGNALR_CONNECTION_SETTING", "AzureSignalRConnectionString")
APPROVAL_TIMEOUT_HOURS = int(os.getenv("APPROVAL_TIMEOUT_HOURS", "24"))

# Agent Framework / Azure OpenAI (managed identity via DefaultAzureCredential)
AZURE_OPENAI_ENDPOINT = _get_required_env("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_CHAT_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME", "gpt-4.1")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

ENABLE_MCP_TOOLS = os.getenv("ENABLE_MCP_TOOLS", "false").lower() in ("true", "1", "yes")

# Hosted Azure MCP server (resource discovery via MI)
MCP_SERVER_URL = os.getenv(
    "MCP_SERVER_URL",
    "",
)
MCP_SERVER_SCOPE = os.getenv("MCP_SERVER_SCOPE", "")

# Daily digest settings
DAILY_DIGEST_CRON = os.getenv("DAILY_DIGEST_CRON", "0 0 8 * * *")  # 8 AM UTC daily
DIGEST_RECIPIENT_EMAIL = _get_required_env("DIGEST_RECIPIENT_EMAIL")
AZURE_SUBSCRIPTION_ID = _get_required_env("AZURE_SUBSCRIPTION_ID")
