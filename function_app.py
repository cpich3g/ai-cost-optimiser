"""Azure Cost Optimiser app entrypoint using Agent Framework + Durable Functions."""

import json
import logging
import os
from datetime import datetime, timezone

import azure.functions as func
from agent_framework._mcp import MCPTool
from agent_framework.azure import AgentFunctionApp, AzureOpenAIChatClient
from azure.identity import DefaultAzureCredential
from mcp.client.sse import sse_client

import config
from activities.signalr import register_signalr_activities
from orchestrators.incident_response import register_orchestrator

logger = logging.getLogger(__name__)

_approval_state: dict[tuple[str, str], str] = {}
_credential = DefaultAzureCredential()

chat_client = AzureOpenAIChatClient(credential=_credential)


# ---------------------------------------------------------------------------
# Legacy SSE MCP tool — the hosted Azure MCP server uses GET-based SSE
# ---------------------------------------------------------------------------

class MCPSSETool(MCPTool):
    """MCP tool using legacy SSE transport with bearer-token auth."""

    def __init__(self, name: str, url: str, *, headers: dict | None = None, **kwargs):
        super().__init__(name=name, **kwargs)
        self.url = url
        self._headers = headers or {}

    def get_mcp_client(self):
        return sse_client(
            url=self.url,
            headers=self._headers,
            timeout=30,
            sse_read_timeout=120,
        )


def _build_mcp_tool() -> MCPSSETool | None:
    """Create an MCPSSETool with MI auth headers for the hosted Azure MCP server."""
    mcp_url = os.getenv("MCP_SERVER_URL", "").strip()
    if not mcp_url:
        return None
    if not mcp_url.endswith("/sse"):
        mcp_url = mcp_url.rstrip("/") + "/sse"

    # Acquire MI token at startup for initial MCP tool config
    mcp_scope = os.getenv("MCP_SERVER_SCOPE", "").strip()
    headers = {}
    if mcp_scope:
        try:
            token = _credential.get_token(mcp_scope)
            headers["Authorization"] = f"Bearer {token.token}"
            logger.info("Acquired MCP bearer token via MI for %s", mcp_scope)
        except Exception as exc:
            logger.warning("Failed to acquire MCP token at startup: %s", exc)

    return MCPSSETool(
        name="azure-mcp",
        url=mcp_url,
        headers=headers,
        description="Azure MCP server — query resource graphs, list resources, and manage Azure infrastructure.",
        request_timeout=120,
        load_prompts=False,
    )


mcp_tool = _build_mcp_tool()

analyzer_agent = chat_client.as_agent(
    name="CostOptimizerAnalyzer",
    instructions=(
        "You are an Azure Cost Optimiser analyst. "
        "Use your Azure MCP tools to discover resources in the specified resource group. "
        "Then generate a JSON object with a recommendations array for cost actions. "
        "Each recommendation must include: actionType (resize|deallocate|delete), "
        "resourceId, riskLevel (low|medium|high|critical), estimatedSavingsMonthlyUsd, and reason."
    ),
    tools=[mcp_tool] if mcp_tool else [],
)

executor_agent = chat_client.as_agent(
    name="CostOptimizerExecutor",
    instructions=(
        "You are an execution planner for approved Azure cost actions. "
        "Return concise execution guidance and risk-aware notes for each action."
    ),
)

app = AgentFunctionApp(
    agents=[analyzer_agent, executor_agent],
    enable_health_check=True,
)

register_orchestrator(app)
register_signalr_activities(app, config.SIGNALR_HUB_NAME, config.SIGNALR_CONNECTION_SETTING)


@app.route(route="cost-optimization/report", methods=["POST"])
@app.durable_client_input(client_name="client")
async def report_cost_optimization(req: func.HttpRequest, client) -> func.HttpResponse:
    """Start a cost optimisation orchestration run."""
    try:
        payload = req.get_json()
    except ValueError:
        return func.HttpResponse(
            body=json.dumps({"error": "Invalid JSON body"}),
            status_code=400,
            mimetype="application/json",
        )

    resources = payload.get("resources", [])
    run_id = payload.get("run_id") or f"costopt-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    instance_id = await client.start_new(
        "cost_optimization_orchestrator",
        instance_id=run_id,
        client_input={
            "run_id": run_id,
            "user_id": payload.get("user_id", "operator"),
            "resources": resources,
        },
    )

    return func.HttpResponse(
        body=json.dumps({"instance_id": instance_id, "status": "started"}),
        status_code=202,
        mimetype="application/json",
    )


@app.route(route="cost-optimization/report-by-group", methods=["POST"])
@app.durable_client_input(client_name="client")
async def report_by_group(req: func.HttpRequest, client) -> func.HttpResponse:
    """Discover resources via Azure ARM (MI), feed to analyzer agent, and start orchestration."""
    try:
        payload = req.get_json()
    except ValueError:
        return func.HttpResponse(
            body=json.dumps({"error": "Invalid JSON body"}),
            status_code=400,
            mimetype="application/json",
        )

    resource_group = payload.get("resource_group") or payload.get("resourceGroup")
    if not resource_group:
        return func.HttpResponse(
            body=json.dumps({"error": "resource_group is required"}),
            status_code=400,
            mimetype="application/json",
        )

    # Step 1: Discover resources using Azure Resource Management (MI-based)
    from azure.mgmt.resource import ResourceManagementClient

    try:
        arm_client = ResourceManagementClient(_credential, os.getenv(
            "AZURE_SUBSCRIPTION_ID",
            "db2cf8dd-6845-470c-84b4-1a3db9946d36",
        ))
        raw_resources = list(arm_client.resources.list_by_resource_group(resource_group))
        resource_list = [
            {
                "id": r.id,
                "name": r.name,
                "type": r.type,
                "location": r.location,
                "kind": r.kind or "",
                "sku": r.sku.name if r.sku else "",
            }
            for r in raw_resources
        ]
    except Exception as exc:
        return func.HttpResponse(
            body=json.dumps({"error": f"ARM resource discovery failed: {exc}"}),
            status_code=502,
            mimetype="application/json",
        )

    if not resource_list:
        return func.HttpResponse(
            body=json.dumps({"error": f"No resources found in resource group '{resource_group}'."}),
            status_code=404,
            mimetype="application/json",
        )

    # Step 2: Feed resource inventory to the analyzer agent for cost analysis
    resource_summary = json.dumps(resource_list, indent=2)
    try:
        prompt = (
            f"Below is the complete resource inventory for Azure resource group '{resource_group}'.\n\n"
            f"```json\n{resource_summary}\n```\n\n"
            f"Analyse each resource for cost optimisation opportunities. "
            f"Return ONLY a JSON object with a 'recommendations' array where each item has:\n"
            f"- actionType: resize | deallocate | delete\n"
            f"- resourceId: the full Azure resource ID\n"
            f"- riskLevel: low | medium | high | critical\n"
            f"- estimatedSavingsMonthlyUsd: numeric estimate\n"
            f"- reason: concise explanation\n"
            f"Include all resources, even if the recommendation is 'no action needed' (use riskLevel 'low' with $0 savings)."
        )
        result = await analyzer_agent.run(prompt)
        agent_output = str(result)
    except Exception as exc:
        inner = getattr(exc, "inner_exception", None) or getattr(exc, "__cause__", None)
        detail = f"{type(exc).__name__}: {exc}"
        if inner:
            detail += f" | Inner: {type(inner).__name__}: {inner}"
        return func.HttpResponse(
            body=json.dumps({"error": f"Agent analysis failed: {detail}"}),
            status_code=502,
            mimetype="application/json",
        )

    run_id = payload.get("run_id") or f"costopt-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    instance_id = await client.start_new(
        "cost_optimization_orchestrator",
        instance_id=run_id,
        client_input={
            "run_id": run_id,
            "user_id": payload.get("user_id", "operator"),
            "resource_group": resource_group,
            "agent_analysis": agent_output,
            "resources": resource_list,
        },
    )

    return func.HttpResponse(
        body=json.dumps({
            "instance_id": instance_id,
            "status": "started",
            "discovered": {
                "resource_group": resource_group,
                "resource_count": len(resource_list),
                "source": "azure-arm-sdk",
            },
            "agent_analysis_preview": agent_output[:2000],
        }),
        status_code=202,
        mimetype="application/json",
    )


@app.route(route="cost-optimization/{instanceId}/decide", methods=["POST"])
@app.durable_client_input(client_name="client")
async def submit_decision(req: func.HttpRequest, client) -> func.HttpResponse:
    """Submit human decision for high-risk action batch."""
    instance_id = req.route_params.get("instanceId")
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            body=json.dumps({"error": "Invalid JSON body"}),
            status_code=400,
            mimetype="application/json",
        )

    decision = body.get("decision")
    if decision not in ("approve", "reject"):
        return func.HttpResponse(
            body=json.dumps({"error": "decision must be 'approve' or 'reject'"}),
            status_code=400,
            mimetype="application/json",
        )

    approval_id = body.get("approvalId")
    stage = body.get("stage", "finance")
    if approval_id:
        _approval_state[(approval_id, stage)] = decision

    await client.raise_event(
        instance_id,
        "ApprovalDecision",
        {
            "decision": decision,
            "stage": stage,
            "approvalId": approval_id,
            "notes": body.get("notes", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    return func.HttpResponse(
        body=json.dumps({"instance_id": instance_id, "decision": decision}),
        status_code=200,
        mimetype="application/json",
    )


@app.route(route="cost-optimization/{instanceId}/status", methods=["GET"])
@app.durable_client_input(client_name="client")
async def get_status(req: func.HttpRequest, client) -> func.HttpResponse:
    """Get durable orchestration status."""
    instance_id = req.route_params.get("instanceId")
    status = await client.get_status(instance_id, show_input=True)
    if not status:
        return func.HttpResponse(
            body=json.dumps({"error": "instance not found"}),
            status_code=404,
            mimetype="application/json",
        )

    return func.HttpResponse(
        body=json.dumps(
            {
                "instance_id": instance_id,
                "runtime_status": status.runtime_status.name if status.runtime_status else None,
                "custom_status": status.custom_status,
                "output": status.output if hasattr(status, "output") else None,
            }
        ),
        status_code=200,
        mimetype="application/json",
    )


@app.route(route="approvals/{approvalId}/{stage}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def get_logicapp_approval(req: func.HttpRequest) -> func.HttpResponse:
    """Polled by Logic App to retrieve approval status for a stage."""
    expected = os.getenv("APPROVAL_CALLBACK_SECRET", "dev-shared-secret")
    token = req.headers.get("x-approval-token", "")
    if token != expected:
        return func.HttpResponse(
            body=json.dumps({"error": "Unauthorized"}),
            status_code=401,
            mimetype="application/json",
        )

    approval_id = req.route_params.get("approvalId")
    stage = req.route_params.get("stage")
    decision = _approval_state.get((approval_id, stage), "pending")

    return func.HttpResponse(
        body=json.dumps({"approvalId": approval_id, "stage": stage, "decision": decision}),
        status_code=200,
        mimetype="application/json",
    )
