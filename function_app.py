"""Azure Cost Optimiser app entrypoint using Agent Framework + Durable Functions."""

import json
import os
from datetime import datetime, timezone

import azure.functions as func
from agent_framework.azure import AgentFunctionApp, AzureOpenAIChatClient
from azure.identity import DefaultAzureCredential

import config
from activities.signalr import register_signalr_activities
from orchestrators.incident_response import register_orchestrator

_approval_state: dict[tuple[str, str], str] = {}

chat_client = AzureOpenAIChatClient(
    credential=DefaultAzureCredential(),
)

analyzer_agent = chat_client.as_agent(
    name="CostOptimizerAnalyzer",
    instructions=(
        "You are an Azure Cost Optimiser analyst. "
        "Generate a JSON object with a recommendations array for cost actions. "
        "Each recommendation must include: actionType (resize|deallocate|delete), "
        "resourceId, riskLevel (low|medium|high|critical), estimatedSavingsMonthlyUsd, and reason."
    ),
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
