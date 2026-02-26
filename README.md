# AI Cost Optimiser - Microsoft Agent Framework Edition

A Python-based Azure automation solution for **AI-powered cloud cost optimization** using Microsoft Agent Framework with Durable Functions orchestration, built-in human approval gates, and real-time notifications.

## Overview

This solution combines the power of AI analysis with robust orchestration to provide:

- **AI-Powered Recommendations**: Uses Microsoft Agent Framework with Azure OpenAI to analyze resources and generate intelligent cost optimization suggestions
- **Risk-Aware Orchestration**: Different approval workflows based on risk assessment
- **Durable Workflows**: Handles long-running processes with state persistence
- **Real-Time Updates**: SignalR integration for live progress notifications
- **Secure Authentication**: Uses Azure Managed Identity for service-to-service calls

> Note: `reference_code/` is reference-only baseline material and is excluded from Azure Function deployment via `.funcignore`.

---

## API Endpoints

### Start Cost Optimization
```http
POST /orchestrators/cost-optimization/start
Content-Type: application/json

{
  "resources": [
    {
      "id": "/subscriptions/.../resourceGroups/.../providers/Microsoft.Compute/virtualMachines/vm1",
      "name": "vm1", 
      "type": "Microsoft.Compute/virtualMachines",
      "utilization": 5.2,
      "monthly_cost": 450.00
    }
  ],
  "run_id": "optional-custom-run-id"
}
```

### Submit Approval Decision
```http
POST /orchestrators/{instanceId}/approval
Content-Type: application/json

{
  "decision": "approve", // or "reject"
  "batch_id": "optional-batch-id",
  "stage": "finance"
}
```

### Query Orchestration Status
```http
GET /orchestrators/{instanceId}/status
```

## Configuration

Required environment variables:

```bash
# Azure OpenAI (required for AI analysis)
AZURE_OPENAI_ENDPOINT=https://your-openai.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=gpt-4
AZURE_OPENAI_API_VERSION=2024-02-01

# SignalR (optional, for real-time notifications)
AzureSignalRConnectionString=your-signalr-connection-string
SIGNALR_HUB_NAME=costoptimiser

# Orchestration timeouts
APPROVAL_TIMEOUT_HOURS=24
REMEDIATION_TIMEOUT_MINUTES=30
```

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure local settings:
```bash
cp local.settings.json.template local.settings.json
# Edit local.settings.json with your configuration
```

3. Run locally:
```bash
func start
```

## Next.js Test UI (shadcn + Tailwind)

A dedicated test UI is available in `dashboard/` to invoke and validate the agent end-to-end.

### 1) Configure UI environment

```bash
cd dashboard
cp .env.example .env.local
```

Set:
- `AGENT_FUNCTION_BASE_URL` (for example `https://func-cost-optimiser-flex8029.azurewebsites.net`)
- `AGENT_FUNCTION_KEY` (function/host key)
- `LOGICAPP_TRIGGER_URL` (Logic App request trigger callback URL)
- `NEXT_PUBLIC_APPROVAL_RECIPIENT` (display-only label in UI)

MCP discovery and auth are handled by the Function App's managed identity — no local MCP credentials needed in the dashboard.

The Function App requires these app settings for MCP:
- `MCP_SERVER_URL` — hosted Azure MCP endpoint
- `MCP_SERVER_SCOPE` — Entra scope for token acquisition (auto-detected from MCP metadata if omitted)

### 2) Run the UI

```bash
npm install
npm run dev
```

Open `http://localhost:3000`.

### 3) What gets tested

- **Agent Orchestration tab**
  - starts runs from a single `resourceGroup` input (Function App discovers resources via MCP + starts orchestration)
  - polls run status (`/api/cost-optimization/{instanceId}/status`)
  - submits approval decisions (`/api/cost-optimization/{instanceId}/decide`)
- **Logic App Email Flow tab**
  - triggers the approval Logic App directly with a test payload

### Approval destination

Finance and engineering approval emails are currently configured to:

- `justinjoy@microsoft.com`

## Workflow

1. **Analysis**: AI agent analyzes provided resources and generates recommendations
2. **Classification**: Actions are classified by risk level (low/medium/high/critical)  
3. **Approval**: High-risk actions require human approval within timeout period
4. **Remediation**: Approved actions are executed sequentially with progress tracking
5. **Notifications**: Real-time updates sent via SignalR throughout the process

## Architecture

- **Microsoft Agent Framework**: AI-powered cost analysis
- **Durable Functions**: Orchestration and approval workflows
- **SignalR**: Real-time notifications
- **Managed Identity**: Secure Azure service authentication

The codebase maintains compatibility with the existing `src/` package while adding new Agent Framework capabilities.
