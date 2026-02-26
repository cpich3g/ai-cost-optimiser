# Azure Cost Optimisation Agent

This Next.js app is Mission Control for end-to-end Azure Cost Optimisation Agent validation.

## Quick start

```bash
cp .env.example .env.local
npm install
npm run dev
```

Open `http://localhost:3000`.

## Required environment variables

- `AGENT_FUNCTION_BASE_URL`
- `AGENT_FUNCTION_KEY`
- `LOGICAPP_TRIGGER_URL`
- `MCP_SERVER_URL`
- `MCP_SERVER_SCOPE`
- `MCP_SERVER_TENANT_ID` (optional override; defaults from MCP metadata)
- `MCP_SERVER_BEARER_TOKEN` (optional override)
- `NEXT_PUBLIC_APPROVAL_RECIPIENT`

## Workflow

Authenticate your local identity for hosted MCP access (one-time per session):

```bash
az login --tenant 16b3c013-d300-468d-ac64-7eda0820b6d3 --scope 60a93168-daca-48be-9866-3e1e07c792f3/Mcp.Tools.ReadWrite
```

1. Enter only the **resource group** and click **Discover + start run**.
2. The dashboard server calls the hosted Azure MCP server, discovers resources for that group, and starts `/api/cost-optimization/report`.
3. Status auto-polls every 5 seconds.
4. Use the decision controls to approve/reject when orchestration reaches approval stages.
