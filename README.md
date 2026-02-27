# Azure Cost Optimiser Agent

An AI-powered agent that periodically scans your Azure subscription, identifies underutilised resources using real monitoring data (Advisor, Activity Logs, Metrics), and proposes a prioritised batch of cost-saving actions — **resize**, **deallocate**, or **delete**. Each action carries a risk level. Engineering reviews the technical plan, Finance approves the savings, then the agent executes a dry run and reports results with Microsoft Learn documentation links.

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Fcpich3g%2Fai-cost-optimiser%2Fmain%2Finfra%2Fbicep%2Fmain.json)

---

## How It Works

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Azure Cost Optimiser Agent                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────┐   ┌────────────┐   ┌─────────────┐   ┌───────────────────┐  │
│  │ Timer /  │──▶│  Discover  │──▶│  SDK Monitor │──▶│  AI Agent Analysis│  │
│  │ Dashboard│   │  Resources │   │  (Advisor,   │   │  (GPT-4.1 via     │  │
│  │ Trigger  │   │  via ARM   │   │   Logs,      │   │   Agent Framework)│  │
│  └──────────┘   └────────────┘   │   Metrics)   │   └────────┬──────────┘  │
│                                  └─────────────┘            │              │
│                                                              ▼              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │              Durable Functions Orchestrator                           │  │
│  │                                                                       │  │
│  │  ┌─────────────┐    ┌──────────────┐    ┌──────────┐    ┌─────────┐  │  │
│  │  │ Engineering │───▶│   Finance    │───▶│  Dry Run  │───▶│ Summary │  │  │
│  │  │  Approval   │    │   Approval   │    │ Execution │    │  Email  │  │  │
│  │  │  (Email)    │    │   (Email)    │    │           │    │         │  │  │
│  │  └─────────────┘    └──────────────┘    └──────────┘    └─────────┘  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌──────────────────────┐  ┌──────────────────────────────────────────────┐│
│  │ Daily Digest (8 AM)  │  │  Logic App ─▶ O365 Outlook ─▶ Email         ││
│  │ All RGs, no approval │  │  (Approve/Reject buttons in email body)     ││
│  └──────────────────────┘  └──────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────┘
```

### Two Flows

| Flow | Trigger | Approval | Output |
|------|---------|----------|--------|
| **Full Optimisation** | Dashboard / API per resource group | Engineering → Finance (email) | Dry-run execution + summary report |
| **Daily Digest** | Timer (8 AM UTC daily) | None | Consolidated email: savings across all RGs |

### What Makes Recommendations Data-Driven

The agent doesn't guess — it cites real monitoring data in every recommendation:

- **Azure Advisor** — authoritative cost recommendations (reserved instances, right-sizing)
- **Activity Logs** — 60-day event history per resource (0 events = truly orphaned → safe to delete)
- **Azure Monitor Metrics** — CPU%, DTU%, TotalRequests, TotalCalls averaged over 60 days
- **SKU/Pricing analysis** — the AI agent applies Azure pricing knowledge for savings estimates

---

## Quick Start

### Prerequisites

- Azure subscription with **Owner** or **Contributor + User Access Administrator** role
- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) installed
- [Azure Functions Core Tools v4](https://learn.microsoft.com/en-us/azure/azure-functions/functions-run-local)
- Python 3.11+
- Node.js 18+ (for the dashboard)
- An Office 365 mailbox for sending approval emails

### Option A: Deploy to Azure (one-click)

1. Click the **Deploy to Azure** button above
2. Fill in the parameters:
   - **Deploy AI Foundry**: `true` to create a new Azure OpenAI resource, `false` if you have one
   - **Azure OpenAI Endpoint**: (only if Deploy AI Foundry = false) your existing endpoint
   - **Azure OpenAI Chat Deployment Name**: (only if Deploy AI Foundry = false) your deployment name
   - **O365 Connection Name**: name for the Office 365 API connection (e.g. `office365`)
   - **Callback Token**: a secret string for email approval links (e.g. `my-secret-2026`)
   - **Email addresses**: engineering, finance, and digest recipient emails
3. After deployment, complete the [post-deploy steps](#post-deploy-steps)

### Option B: Deploy via CLI

```bash
# 1. Clone and navigate
git clone https://github.com/cpich3g/ai-cost-optimiser.git
cd ai-cost-optimiser

# 2. Create resource group
az group create --name rg-cost-optimiser --location swedencentral

# 3. Deploy infrastructure (with new AI Foundry)
az deployment group create \
  --resource-group rg-cost-optimiser \
  --template-file infra/bicep/main.bicep \
  --parameters \
    deployAIFoundry=true \
    o365ConnectionName=office365 \
    callbackToken='your-secret-token' \
    digestRecipientEmail='you@company.com' \
    engineeringEmail='eng-team@company.com' \
    financeEmail='finance@company.com'

# Or with an EXISTING Azure OpenAI resource:
az deployment group create \
  --resource-group rg-cost-optimiser \
  --template-file infra/bicep/main.bicep \
  --parameters \
    deployAIFoundry=false \
    azureOpenAiEndpoint='https://my-openai.openai.azure.com/' \
    azureOpenAiChatDeploymentName='gpt-4.1' \
    o365ConnectionName=office365 \
    callbackToken='your-secret-token' \
    digestRecipientEmail='you@company.com' \
    engineeringEmail='eng-team@company.com' \
    financeEmail='finance@company.com'

# 4. Get the Function App name from output
FUNC_APP=$(az deployment group show \
  --resource-group rg-cost-optimiser \
  --name main \
  --query 'properties.outputs.functionAppName.value' -o tsv)

# 5. Grant Function App Reader on the subscription (for resource discovery)
PRINCIPAL_ID=$(az deployment group show \
  --resource-group rg-cost-optimiser \
  --name main \
  --query 'properties.outputs.functionAppPrincipalId.value' -o tsv)

az role assignment create \
  --assignee-object-id $PRINCIPAL_ID \
  --assignee-principal-type ServicePrincipal \
  --role Reader \
  --scope /subscriptions/$(az account show --query id -o tsv)

# 6. Deploy the function code
func azure functionapp publish $FUNC_APP --python
```

### Post-Deploy Steps

1. **Authorise the Office 365 connector** — In the Azure Portal, navigate to the Logic App → API Connections → `office365` → Edit → Authorise with your O365 account
2. **Set the Logic App trigger URL** on the Function App:
   ```bash
   # Get the trigger URL from the Logic App
   TRIGGER_URL=$(az rest --method post \
     --uri "/subscriptions/{sub}/resourceGroups/rg-cost-optimiser/providers/Microsoft.Logic/workflows/{logicAppName}/triggers/When_an_HTTP_request_is_received/listCallbackUrl?api-version=2016-06-01" \
     --query value -o tsv)
   
   az functionapp config appsettings set \
     --name $FUNC_APP \
     --resource-group rg-cost-optimiser \
     --settings "LOGICAPP_TRIGGER_URL=$TRIGGER_URL"
   ```
3. **Grant Cognitive Services OpenAI User** (if using an existing AI Foundry resource):
   ```bash
   az role assignment create \
     --assignee-object-id $PRINCIPAL_ID \
     --assignee-principal-type ServicePrincipal \
     --role "Cognitive Services OpenAI User" \
     --scope /subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.CognitiveServices/accounts/{name}
   ```

---

## API Reference

### Full Optimisation Run (per resource group)

```http
POST /api/cost-optimization/report-by-group
Content-Type: application/json

{
  "resource_group": "my-resource-group"
}
```

Returns `202` with `instance_id`. The orchestrator:
1. Discovers resources via ARM
2. Gathers monitoring data (Advisor + Activity Logs + Metrics)
3. Runs AI analysis
4. Sends engineering approval email
5. Waits for approval → sends finance email
6. Waits for approval → executes dry run
7. Researches MS Learn docs
8. Sends summary email to both teams

### Daily Digest (manual trigger)

```http
POST /api/cost-optimization/digest
Content-Type: application/json

{
  "resource_groups": ["rg-1", "rg-2"]  // optional — omit to scan ALL RGs
}
```

### Check Status

```http
GET /api/cost-optimization/{instanceId}/status
```

### Submit Decision (from dashboard)

```http
POST /api/cost-optimization/{instanceId}/decide
Content-Type: application/json

{
  "decision": "approve",
  "stage": "engineering"
}
```

---

## Dashboard

A Next.js dashboard is included in `dashboard/` for triggering runs and monitoring status.

```bash
cd dashboard
cp .env.example .env.local
# Edit .env.local with your Function App URL and key
npm install
npm run dev
```

Open `http://localhost:3000`. Enter a resource group name, click **Run Analysis**, and watch the orchestration progress through each stage.

---

## Configuration

### Function App Settings

| Setting | Required | Default | Description |
|---------|----------|---------|-------------|
| `AZURE_OPENAI_ENDPOINT` | ✅ | — | Azure OpenAI endpoint URL |
| `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME` | ✅ | `gpt-4.1` | Model deployment name |
| `AZURE_SUBSCRIPTION_ID` | ✅ | — | Subscription to scan |
| `LOGICAPP_TRIGGER_URL` | ✅ | — | Logic App HTTP trigger URL for emails |
| `APPROVAL_CALLBACK_SECRET` | ✅ | — | Secret for email approve/reject links |
| `ENGINEERING_EMAIL` | ✅ | — | Engineering team email |
| `FINANCE_EMAIL` | ✅ | — | Finance team email |
| `DIGEST_RECIPIENT_EMAIL` | ✅ | — | Daily digest recipient |
| `APPROVAL_TIMEOUT_HOURS` | | `24` | Hours before approval expires |
| `DAILY_DIGEST_CRON` | | `0 0 8 * * *` | CRON schedule for daily digest (default: 8 AM UTC) |

### Dashboard Settings (`dashboard/.env.local`)

| Setting | Description |
|---------|-------------|
| `AGENT_FUNCTION_BASE_URL` | Function App URL (e.g. `https://func-costopt-xxx.azurewebsites.net`) |
| `AGENT_FUNCTION_KEY` | Function or host key for authentication |
| `LOGICAPP_TRIGGER_URL` | Logic App trigger URL |
| `NEXT_PUBLIC_APPROVAL_RECIPIENT` | Display label for approval recipient |

---

## Cost Estimate

Running this agent costs very little thanks to serverless and pay-per-use pricing. Here are T-shirt-sized estimates based on [Azure pricing](https://azure.microsoft.com/en-us/pricing/) as of early 2026.

### 🟢 Small (1 RG, on-demand only)

> Personal or single-project use. No daily digest.

| Service | Usage | Monthly Cost |
|---------|-------|-------------|
| Azure Functions (Flex Consumption) | ~50 executions/mo, ~200 GB-s | **Free** (within free grant) |
| Azure OpenAI (GPT-4.1) | ~2 calls/run × 10K tokens | **~$0.10** |
| Logic App (Consumption) | ~3 emails/run | **~$0.01** |
| Storage (Durable state) | < 1 GB | **~$0.02** |
| App Insights | < 5 GB ingested | **Free** (5 GB/mo free) |
| **Total** | | **~$0.15/mo** |

### 🟡 Medium (10 RGs, daily digest)

> Team or department use. Daily digest + occasional full runs.

| Service | Usage | Monthly Cost |
|---------|-------|-------------|
| Azure Functions (Flex Consumption) | ~1,500 exec/mo, ~6,000 GB-s | **~$0.20** |
| Azure OpenAI (GPT-4.1) | ~10 calls/day × 15K tokens | **~$3.50** |
| Logic App (Consumption) | ~30 digest + ~20 approval emails | **~$0.02** |
| Storage (Durable state) | < 1 GB | **~$0.02** |
| App Insights | < 5 GB ingested | **Free** |
| AI Foundry resource (S0) | Hosting the endpoint | **Free** (pay per token only) |
| **Total** | | **~$4/mo** |

### 🔴 Large (50+ RGs, daily digest + frequent full runs)

> Enterprise / multi-team. Daily digest across entire subscription + weekly full optimisation runs.

| Service | Usage | Monthly Cost |
|---------|-------|-------------|
| Azure Functions (Flex Consumption) | ~10,000 exec/mo, ~40,000 GB-s | **~$1.50** |
| Azure OpenAI (GPT-4.1) | ~50 calls/day × 20K tokens | **~$20** |
| Logic App (Consumption) | ~30 digest + ~100 approval emails | **~$0.05** |
| Storage (Durable state) | 1-2 GB | **~$0.05** |
| App Insights | ~10 GB ingested | **~$11.50** |
| AI Foundry resource (S0) | Hosting the endpoint | **Free** (pay per token only) |
| **Total** | | **~$35/mo** |

> **💡 The agent typically identifies 10-100× its own cost in savings.** In our test run across 49 resource groups (553 resources), it found **$5,692/mo ($68K/year)** in potential savings — while costing ~$4/mo to run.

---

## Project Structure

```
ai-cost-optimiser/
├── function_app.py              # Main entry: HTTP endpoints, timer trigger, agent setup
├── config.py                    # Central config (env vars, defaults)
├── orchestrators/
│   ├── incident_response.py     # Full optimisation orchestrator (2-stage approval)
│   └── daily_digest.py          # Digest orchestrator (fan-out/fan-in, no approval)
├── activities/
│   └── signalr.py               # Email templates + Logic App trigger
├── infra/
│   └── bicep/
│       ├── main.bicep           # Infrastructure-as-code (Functions, Logic App, AI Foundry)
│       ├── main.json            # Compiled ARM template (Deploy to Azure button)
│       └── logicapp-approval.bicep
├── logicapps/
│   └── approval-workflow.json   # Logic App workflow definition
├── dashboard/                   # Next.js dashboard UI
├── deploy.ps1                   # Deploy script with pre-deploy instance purge
├── requirements.txt             # Python dependencies
├── host.json                    # Durable Functions config
└── tests/                       # Test suite
```

---

## Deployment Script

The `deploy.ps1` script handles safe deployments by purging stale durable instances first:

```powershell
.\deploy.ps1                    # Purge stale instances + deploy
.\deploy.ps1 -SkipPurge         # Deploy without purge
```

This prevents non-deterministic replay errors and phantom email loops when orchestrator code changes while instances are mid-flight.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| **AI Analysis** | [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) + Azure OpenAI GPT-4.1 |
| **Orchestration** | Azure Durable Functions (Python, Flex Consumption) |
| **Monitoring Data** | Azure SDK — `azure-mgmt-advisor`, `azure-mgmt-monitor`, `azure-mgmt-resource` |
| **Email Approvals** | Azure Logic Apps + Office 365 Outlook connector |
| **Authentication** | Managed Identity (zero credentials in code) |
| **Dashboard** | Next.js 15 + Tailwind CSS + shadcn/ui |
| **Infrastructure** | Bicep / ARM templates |

---

## License

MIT

> **Note:** `reference_code/` is reference-only baseline material and is excluded from deployment via `.funcignore`.
