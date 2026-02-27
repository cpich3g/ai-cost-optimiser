"""Azure Cost Optimiser app entrypoint using Agent Framework + Durable Functions."""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import azure.functions as func
from agent_framework._mcp import MCPTool, MCPStreamableHTTPTool
from agent_framework.azure import AgentFunctionApp, AzureOpenAIChatClient
from azure.identity import DefaultAzureCredential
from mcp.client.sse import sse_client

import config
from activities.signalr import register_signalr_activities
from orchestrators.incident_response import register_orchestrator
from orchestrators.daily_digest import register_digest_orchestrator

logger = logging.getLogger(__name__)

_credential = DefaultAzureCredential()

chat_client = AzureOpenAIChatClient(credential=_credential)


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------

def _validate_resource_group_name(name: str) -> bool:
    """Validate Azure resource group name format.

    Azure resource group names must:
    - Be 1-90 characters
    - Only contain alphanumerics, underscores, parentheses, hyphens, periods
    - Not end with period
    """
    if not name or len(name) > 90:
        return False
    if name.endswith('.'):
        return False
    # Azure resource group name pattern
    pattern = r'^[a-zA-Z0-9._()-]+$'
    return bool(re.match(pattern, name))


def _validate_instance_id(instance_id: str) -> bool:
    """Validate durable orchestration instance ID format."""
    if not instance_id or len(instance_id) > 256:
        return False
    # Allow alphanumerics, hyphens, underscores
    pattern = r'^[a-zA-Z0-9_-]+$'
    return bool(re.match(pattern, instance_id))


# ---------------------------------------------------------------------------
# Azure SDK helpers — direct monitoring data collection (bypasses MCP sampling)
# ---------------------------------------------------------------------------

async def _gather_monitoring_data(subscription_id: str, resource_group: str, resources: list) -> dict:
    """Gather Advisor recommendations, Activity Logs, and key metrics via Azure SDK.

    Uses asyncio to parallelize API calls for improved performance.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from azure.mgmt.advisor import AdvisorManagementClient
    from azure.mgmt.monitor import MonitorManagementClient

    advisor_client = AdvisorManagementClient(_credential, subscription_id)
    monitor_client = MonitorManagementClient(_credential, subscription_id)

    result = {"advisor_recommendations": [], "activity_logs": {}, "metrics": {}}

    # Run Advisor, Activity Logs, and Metrics queries in parallel using thread pool
    # (Azure SDK clients are sync, so we use threads to parallelize)
    loop = asyncio.get_event_loop()

    def _get_advisor_recommendations():
        """Fetch Advisor cost recommendations."""
        recs_list = []
        try:
            recs = advisor_client.recommendations.list(filter="Category eq 'Cost'")
            for rec in recs:
                rid = ""
                if rec.resource_metadata and rec.resource_metadata.resource_id:
                    rid = rec.resource_metadata.resource_id
                impacted = getattr(rec, "impacted_field", "") or ""
                recs_list.append({
                    "resourceId": rid,
                    "impactedField": impacted,
                    "impactedValue": getattr(rec, "impacted_value", "") or "",
                    "category": str(rec.category),
                    "impact": str(rec.impact),
                    "problem": rec.short_description.problem if rec.short_description else "",
                    "solution": rec.short_description.solution if rec.short_description else "",
                })
        except Exception as exc:
            logger.warning("Advisor query failed: %s", exc)
        return recs_list

    def _get_activity_logs():
        """Fetch Activity Logs for the resource group."""
        logs_dict = {}
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(days=60)
        try:
            logs = monitor_client.activity_logs.list(
                filter=(
                    f"eventTimestamp ge '{start_time.isoformat()}' "
                    f"and eventTimestamp le '{now.isoformat()}' "
                    f"and resourceGroupName eq '{resource_group}'"
                ),
            )
            for log_entry in logs:
                rid = log_entry.resource_id or ""
                name = rid.split("/")[-1] if rid else "unknown"
                if name not in logs_dict:
                    logs_dict[name] = {"count": 0, "last_event": None}
                logs_dict[name]["count"] += 1
                ts = log_entry.event_timestamp
                if ts:
                    ts_str = ts.isoformat()
                    prev = logs_dict[name]["last_event"]
                    if not prev or ts_str > prev:
                        logs_dict[name]["last_event"] = ts_str
        except Exception as exc:
            logger.warning("Activity Logs query failed: %s", exc)
        return logs_dict

    def _get_metrics_for_resource(res, metric_name):
        """Fetch metrics for a single resource."""
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(days=60)
        try:
            ts_start = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
            ts_end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            metrics_data = monitor_client.metrics.list(
                resource_uri=res["id"],
                timespan=f"{ts_start}/{ts_end}",
                interval="P1D",
                metricnames=metric_name,
                aggregation="Average",
            )
            values = []
            for m in metrics_data.value:
                for ts_entry in m.timeseries:
                    for dp in ts_entry.data:
                        if dp.average is not None:
                            values.append(dp.average)
            if values:
                # Compute stats in single pass for efficiency
                total = sum(values)
                count = len(values)
                return {
                    "name": res["name"],
                    "metric": metric_name,
                    "avg": round(total / count, 2),
                    "max": round(max(values), 2),
                    "min": round(min(values), 2),
                    "datapoints": count,
                }
        except Exception as exc:
            logger.warning("Metrics query failed for %s (%s): %s", res["name"], res.get("type"), exc)
        return None

    # Parallelize the three main queries
    with ThreadPoolExecutor(max_workers=10) as executor:
        # Submit Advisor and Activity Logs tasks
        advisor_future = loop.run_in_executor(executor, _get_advisor_recommendations)
        activity_logs_future = loop.run_in_executor(executor, _get_activity_logs)

        # Submit metrics tasks for each metrizable resource
        metrizable_types = {
            "Microsoft.Compute/virtualMachines": "Percentage CPU",
            "Microsoft.Web/sites": "CpuPercentage",
            "Microsoft.Web/serverFarms": "CpuPercentage",
            "Microsoft.Sql/servers/databases": "dtu_consumption_percent",
            "Microsoft.Cache/Redis": "usedmemorypercentage",
            "Microsoft.DocumentDb/databaseAccounts": "TotalRequests",
            "Microsoft.CognitiveServices/accounts": "TotalCalls",
            "Microsoft.Storage/storageAccounts": "Transactions",
            "Microsoft.ContainerRegistry/registries": "TotalPullCount",
        }

        metrics_futures = []
        for res in resources:
            rtype = res.get("type", "")
            if rtype in metrizable_types:
                metric_name = metrizable_types[rtype]
                metrics_futures.append(
                    loop.run_in_executor(executor, _get_metrics_for_resource, res, metric_name)
                )

        # Wait for all tasks to complete
        result["advisor_recommendations"] = await advisor_future
        result["activity_logs"] = await activity_logs_future

        if metrics_futures:
            metrics_results = await asyncio.gather(*metrics_futures)
            for metric_result in metrics_results:
                if metric_result:
                    result["metrics"][metric_result["name"]] = metric_result

    return result


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


def _build_mslearn_tool() -> MCPStreamableHTTPTool | None:
    """Create an MCP tool for the Microsoft Learn documentation server."""
    mslearn_url = os.getenv(
        "MSLEARN_MCP_URL", "https://learn.microsoft.com/api/mcp"
    ).strip()
    if not mslearn_url:
        return None
    try:
        return MCPStreamableHTTPTool(
            name="mslearn-docs",
            url=mslearn_url,
            description="Microsoft Learn documentation — search Azure docs, best practices, and reference guides.",
            request_timeout=30,
            load_prompts=False,
        )
    except Exception as exc:
        logger.warning("Failed to create MS Learn MCP tool: %s", exc)
        return None


mcp_tool = _build_mcp_tool()
mslearn_tool = _build_mslearn_tool()

analyzer_agent = chat_client.as_agent(
    name="CostOptimizerAnalyzer",
    instructions=(
        "You are an Azure Cost Optimiser analyst. You receive:\n"
        "1. A resource inventory (from ARM)\n"
        "2. Monitoring data: Advisor cost recs, Activity Logs (event counts per resource), "
        "and CPU/DTU/memory metrics (avg, min, max over 60 days)\n\n"
        "Use this data to make precise recommendations:\n"
        "- 'resize': Cite metrics (e.g. 'avg CPU 3.2%') or Advisor recommendation.\n"
        "- 'deallocate': Cite <5% avg CPU or zero activity.\n"
        "- 'delete': ONLY if 0 Activity Log events in 60 days (orphaned). Always state why safe.\n"
        "- 'no action needed': Free-tier, essential infra, or healthy utilisation.\n\n"
        "Every reason must cite specific data from the monitoring payload.\n"
        "Estimate monthly savings using your Azure pricing knowledge.\n\n"
        "Return JSON: {\"recommendations\": [{\"actionType\":\"...\",\"resourceId\":\"...\","
        "\"riskLevel\":\"low|medium|high|critical\",\"estimatedSavingsMonthlyUsd\":0,"
        "\"reason\":\"explanation citing monitoring data\"}]}"
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

doc_research_agent = chat_client.as_agent(
    name="DocResearchAgent",
    instructions=(
        "You are a documentation researcher for Azure cost optimisation. "
        "Given a list of Azure cost actions and resource types, return the most relevant "
        "Microsoft Learn documentation URLs. "
        "Always return a JSON object: {\"docs\": [{\"title\":\"...\",\"url\":\"https://learn.microsoft.com/...\",\"relevance\":\"...\"}]}. "
        "Include docs for: Azure Advisor cost recommendations, Azure cost management best practices, "
        "resource-specific sizing/SKU guides, and how-to guides for each action type (resize, deallocate, delete). "
        "Use ONLY real Microsoft Learn URLs that you are confident exist."
    ),
    tools=[],
)

app = AgentFunctionApp(
    agents=[analyzer_agent, executor_agent, doc_research_agent],
    enable_health_check=True,
)

register_orchestrator(app)
register_digest_orchestrator(app)
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

    # Validate resource group name format to prevent injection
    if not _validate_resource_group_name(resource_group):
        return func.HttpResponse(
            body=json.dumps({"error": "Invalid resource_group name format"}),
            status_code=400,
            mimetype="application/json",
        )

    # Step 1: Discover resources using Azure Resource Management (MI-based)
    from azure.mgmt.resource import ResourceManagementClient

    try:
        arm_client = ResourceManagementClient(_credential, config.AZURE_SUBSCRIPTION_ID)
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

    # Step 2: Gather monitoring data via Azure SDK (Advisor, Activity Logs, Metrics)
    try:
        monitoring = await _gather_monitoring_data(config.AZURE_SUBSCRIPTION_ID, resource_group, resource_list)
        logger.info(
            "Monitoring data: %d advisor recs, %d resources with activity, %d with metrics",
            len(monitoring["advisor_recommendations"]),
            len(monitoring["activity_logs"]),
            len(monitoring["metrics"]),
        )
    except Exception as exc:
        logger.warning("Monitoring data gathering failed: %s", exc)
        monitoring = {"advisor_recommendations": [], "activity_logs": {}, "metrics": {}}

    # Step 3: Feed resource inventory + monitoring data to the analyzer agent
    resource_summary = json.dumps(resource_list, indent=2)
    monitoring_summary = json.dumps(monitoring, indent=2)
    try:
        prompt = (
            f"Analyse Azure resource group '{resource_group}' for cost optimisation.\n\n"
            f"RESOURCE INVENTORY:\n```json\n{resource_summary}\n```\n\n"
            f"MONITORING DATA (collected via Azure SDK):\n```json\n{monitoring_summary}\n```\n\n"
            f"The monitoring data includes:\n"
            f"- Azure Advisor cost recommendations (authoritative — act on these)\n"
            f"- Activity Logs per resource (event count + last event date over 60 days)\n"
            f"- CPU/DTU/memory metrics for compute resources (avg, min, max over 60 days)\n\n"
            f"RULES:\n"
            f"- If Advisor recommends an action for a resource, follow it and cite Advisor.\n"
            f"- 'delete' ONLY if Activity Logs show 0 events over 60 days (truly orphaned).\n"
            f"- 'resize' if metrics show <20% avg utilisation OR SKU is clearly oversized.\n"
            f"- 'deallocate' if compute metrics show <5% avg CPU.\n"
            f"- 'no action needed' for free-tier, essential infra, or healthy utilisation.\n"
            f"- Cite actual data: 'Advisor recommends...', 'avg CPU 3.2% over 60d', "
            f"'0 Activity Log events since <date>', 'current SKU S0 costs ~$X/mo'.\n\n"
            f"Return ONLY JSON:\n"
            f"{{\"recommendations\": [{{\"actionType\":\"...\",\"resourceId\":\"...\","
            f"\"riskLevel\":\"low|medium|high|critical\",\"estimatedSavingsMonthlyUsd\":0,"
            f"\"reason\":\"cite monitoring data or SKU analysis\"}}]}}\n"
            f"Include ALL resources."
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
    """Submit human decision for high-risk action batch (from dashboard UI)."""
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

    stage = body.get("stage", "finance").lower()
    event_name = "EngineeringDecision" if stage == "engineering" else "FinanceDecision"

    await client.raise_event(
        instance_id,
        event_name,
        {
            "decision": decision,
            "stage": stage,
            "approvalId": body.get("approvalId", ""),
            "notes": body.get("notes", ""),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    return func.HttpResponse(
        body=json.dumps({"instance_id": instance_id, "decision": decision}),
        status_code=200,
        mimetype="application/json",
    )


@app.route(route="cost-optimization/{instanceId}/email-decide", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@app.durable_client_input(client_name="client")
async def email_decision(req: func.HttpRequest, client) -> func.HttpResponse:
    """Handle approve/reject clicks from email links (GET with query params)."""
    import html

    instance_id = req.route_params.get("instanceId")
    decision = req.params.get("decision", "").lower()
    function_key = req.params.get("key", "")
    stage = req.params.get("stage", "finance").lower()

    expected_key = config.APPROVAL_CALLBACK_SECRET
    if function_key != expected_key:
        return func.HttpResponse(
            body="<html><body><h2>Unauthorized</h2><p>Invalid approval link.</p></body></html>",
            status_code=401,
            mimetype="text/html",
        )

    if decision not in ("approve", "reject"):
        return func.HttpResponse(
            body="<html><body><h2>Invalid decision</h2><p>Use approve or reject.</p></body></html>",
            status_code=400,
            mimetype="text/html",
        )

    # Route to the correct durable event based on approval stage
    event_name = "EngineeringDecision" if stage == "engineering" else "FinanceDecision"

    try:
        await client.raise_event(
            instance_id,
            event_name,
            {
                "decision": decision,
                "stage": stage,
                "notes": f"Decided via {stage} email link",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as exc:
        logger.warning("raise_event failed for %s: %s", instance_id, exc)
        # HTML-escape instance_id to prevent XSS
        safe_instance_id = html.escape(instance_id or "unknown")
        return func.HttpResponse(
            body=f"""<html><body style="font-family:Segoe UI,sans-serif;text-align:center;padding:60px">
            <div style="font-size:64px">⏰</div>
            <h1 style="color:#f59e0b">Link Expired</h1>
            <p>The orchestration <strong>{safe_instance_id}</strong> has already completed or expired.</p>
            <p style="color:#888;font-size:14px">No action was taken. You can close this tab.</p>
            </body></html>""",
            status_code=200,
            mimetype="text/html",
        )

    emoji = "✅" if decision == "approve" else "❌"
    color = "#10b981" if decision == "approve" else "#ef4444"
    stage_label = stage.capitalize()
    # HTML-escape instance_id to prevent XSS
    safe_instance_id = html.escape(instance_id or "unknown")
    return func.HttpResponse(
        body=f"""<html><body style="font-family:Segoe UI,sans-serif;text-align:center;padding:60px">
        <div style="font-size:64px">{emoji}</div>
        <h1 style="color:{color}">{stage_label} Decision: {decision.upper()}</h1>
        <p>Your {stage_label.lower()} decision for <strong>{safe_instance_id}</strong> has been recorded.</p>
        <p style="color:#888;font-size:14px">You can close this tab.</p>
        </body></html>""",
        status_code=200,
        mimetype="text/html",
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


# ---------------------------------------------------------------------------
# Daily Digest — Timer trigger (8 AM UTC) and manual HTTP trigger
# ---------------------------------------------------------------------------

@app.timer_trigger(schedule=config.DAILY_DIGEST_CRON, arg_name="timer", run_on_startup=False)
@app.durable_client_input(client_name="client")
async def daily_digest_timer(timer: func.TimerRequest, client) -> None:
    """Auto-scheduled daily cost digest across all resource groups."""
    from azure.mgmt.resource import ResourceManagementClient

    subscription_id = config.AZURE_SUBSCRIPTION_ID
    logger.info("Daily digest triggered for subscription %s", subscription_id)

    try:
        arm = ResourceManagementClient(_credential, subscription_id)
        rg_list = [rg.name for rg in arm.resource_groups.list()]
    except Exception as exc:
        logger.error("Failed to list resource groups: %s", exc)
        return

    if not rg_list:
        logger.info("No resource groups found — skipping digest.")
        return

    run_id = f"digest-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    await client.start_new(
        "daily_digest_orchestrator",
        instance_id=run_id,
        client_input={
            "run_id": run_id,
            "subscription_id": subscription_id,
            "resource_groups": rg_list,
        },
    )
    logger.info("Daily digest started: %s (%d RGs)", run_id, len(rg_list))


@app.route(route="cost-optimization/digest", methods=["POST"])
@app.durable_client_input(client_name="client")
async def trigger_digest(req: func.HttpRequest, client) -> func.HttpResponse:
    """Manually trigger a cost digest (same as the daily timer)."""
    from azure.mgmt.resource import ResourceManagementClient

    subscription_id = config.AZURE_SUBSCRIPTION_ID

    try:
        payload = req.get_json()
    except ValueError:
        payload = {}

    # Allow filtering to specific RGs, or scan all
    rg_filter = payload.get("resource_groups", [])
    if not rg_filter:
        try:
            arm = ResourceManagementClient(_credential, subscription_id)
            rg_filter = [rg.name for rg in arm.resource_groups.list()]
        except Exception as exc:
            return func.HttpResponse(
                body=json.dumps({"error": f"Failed to list resource groups: {exc}"}),
                status_code=502,
                mimetype="application/json",
            )

    run_id = payload.get("run_id") or f"digest-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    await client.start_new(
        "daily_digest_orchestrator",
        instance_id=run_id,
        client_input={
            "run_id": run_id,
            "subscription_id": subscription_id,
            "resource_groups": rg_filter,
        },
    )

    return func.HttpResponse(
        body=json.dumps({
            "instance_id": run_id,
            "status": "started",
            "resource_groups": rg_filter,
            "rg_count": len(rg_filter),
        }),
        status_code=202,
        mimetype="application/json",
    )

