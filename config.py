"""Central configuration for the Azure Cost Optimiser function app."""

import os

APPROVAL_CALLBACK_SECRET = os.getenv("APPROVAL_CALLBACK_SECRET", "dev-shared-secret")
SIGNALR_HUB_NAME = os.getenv("SIGNALR_HUB_NAME", "costoptimiser")
SIGNALR_CONNECTION_SETTING = os.getenv("SIGNALR_CONNECTION_SETTING", "AzureSignalRConnectionString")
APPROVAL_TIMEOUT_HOURS = int(os.getenv("APPROVAL_TIMEOUT_HOURS", "24"))

# Agent Framework / Azure OpenAI (managed identity via DefaultAzureCredential)
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_CHAT_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME", "gpt-4.1")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

ENABLE_MCP_TOOLS = os.getenv("ENABLE_MCP_TOOLS", "false").lower() in ("true", "1", "yes")

# Hosted Azure MCP server (resource discovery via MI)
MCP_SERVER_URL = os.getenv(
    "MCP_SERVER_URL",
    "https://ca-azure-mcp-server.greenground-a1172782.swedencentral.azurecontainerapps.io",
)
MCP_SERVER_SCOPE = os.getenv("MCP_SERVER_SCOPE", "")
