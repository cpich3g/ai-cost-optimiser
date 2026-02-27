"""Durable orchestrator for daily cost digest — no approvals, report-only.

Fan-out: analyse each resource group in parallel via sub-orchestrations.
Fan-in:  aggregate results and send a single consolidated digest email.
"""

from __future__ import annotations

import json
import re


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", stripped, re.DOTALL)
    if fence:
        return json.loads(fence.group(1).strip())
    obj = re.search(r"\{.*\}", stripped, re.DOTALL)
    if obj:
        return json.loads(obj.group(0))
    raise json.JSONDecodeError("No JSON object found", stripped, 0)


def register_digest_orchestrator(app):
    """Register the daily digest orchestrator and its per-RG analysis activity."""

    @app.activity_trigger(input_name="payload")
    def analyse_resource_group(payload: dict) -> dict:
        """Analyse a single resource group — runs outside durable context."""
        import logging
        import os
        from datetime import datetime, timedelta, timezone

        from agent_framework.azure import AzureOpenAIChatClient
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.resource import ResourceManagementClient

        logger = logging.getLogger(__name__)
        rg = payload["resource_group"]
        subscription_id = payload["subscription_id"]

        credential = DefaultAzureCredential()
        arm = ResourceManagementClient(credential, subscription_id)

        # Discover resources
        try:
            raw = list(arm.resources.list_by_resource_group(rg))
            resources = [
                {
                    "id": r.id, "name": r.name, "type": r.type,
                    "location": r.location, "kind": r.kind or "",
                    "sku": r.sku.name if r.sku else "",
                }
                for r in raw
            ]
        except Exception as exc:
            logger.warning("ARM discovery failed for %s: %s", rg, exc)
            return {"resource_group": rg, "error": str(exc), "recommendations": []}

        if not resources:
            return {"resource_group": rg, "resource_count": 0, "recommendations": []}

        # Gather monitoring data (reuse the SDK helper from function_app)
        from function_app import _gather_monitoring_data

        try:
            monitoring = _gather_monitoring_data(subscription_id, rg, resources)
        except Exception as exc:
            logger.warning("Monitoring data failed for %s: %s", rg, exc)
            monitoring = {"advisor_recommendations": [], "activity_logs": {}, "metrics": {}}

        # Run agent analysis
        resource_json = json.dumps(resources, indent=2)
        monitoring_json = json.dumps(monitoring, indent=2)

        prompt = (
            f"Analyse Azure resource group '{rg}' for cost optimisation.\n\n"
            f"RESOURCE INVENTORY:\n```json\n{resource_json}\n```\n\n"
            f"MONITORING DATA:\n```json\n{monitoring_json}\n```\n\n"
            f"RULES:\n"
            f"- 'delete' ONLY if Activity Logs show 0 events over 60 days.\n"
            f"- 'resize' if metrics show <20% avg utilisation or SKU is oversized.\n"
            f"- 'deallocate' if compute metrics show <5% avg CPU.\n"
            f"- Cite actual data in every reason.\n"
            f"- Estimate monthly savings.\n\n"
            f"Return ONLY JSON:\n"
            f"{{\"recommendations\": [{{\"actionType\":\"...\",\"resourceId\":\"...\","
            f"\"riskLevel\":\"low|medium|high|critical\",\"estimatedSavingsMonthlyUsd\":0,"
            f"\"reason\":\"cite monitoring data\"}}]}}\n"
            f"Include ALL resources."
        )

        try:
            import asyncio

            chat_client = AzureOpenAIChatClient(credential=credential)
            agent = chat_client.as_agent(
                name="DigestAnalyzer",
                instructions=(
                    "You are an Azure Cost Optimiser analyst. Use the provided monitoring "
                    "data (Advisor recs, Activity Logs, Metrics) to make data-driven "
                    "recommendations. Every reason must cite specific data."
                ),
            )
            result = asyncio.get_event_loop().run_until_complete(agent.run(prompt))
            recs = _extract_json(str(result)).get("recommendations", [])
        except Exception as exc:
            logger.warning("Agent analysis failed for %s: %s", rg, exc)
            recs = []

        actionable = [
            r for r in recs
            if str(r.get("actionType", "")).lower() not in ("no action needed", "none", "")
        ]
        total_savings = round(
            sum(float(a.get("estimatedSavingsMonthlyUsd", 0) or 0) for a in actionable), 2
        )

        return {
            "resource_group": rg,
            "resource_count": len(resources),
            "recommendation_count": len(recs),
            "actionable_count": len(actionable),
            "estimated_savings_monthly": total_savings,
            "recommendations": actionable,
        }

    @app.orchestration_trigger(context_name="context")
    def daily_digest_orchestrator(context):
        """Fan-out across resource groups, fan-in results, send digest email."""
        input_data = context.get_input() or {}
        resource_groups = input_data.get("resource_groups", [])
        subscription_id = input_data.get("subscription_id", "")
        run_id = input_data.get("run_id", context.instance_id)

        context.set_custom_status({"stage": "analyzing", "run_id": run_id, "rg_count": len(resource_groups)})

        # Fan-out: analyse each RG in parallel
        tasks = []
        for rg in resource_groups:
            task = context.call_activity(
                "analyse_resource_group",
                {"resource_group": rg, "subscription_id": subscription_id},
            )
            tasks.append(task)

        # Fan-in: wait for all analyses
        results = yield context.task_all(tasks)

        # Aggregate
        context.set_custom_status({"stage": "sending_digest", "run_id": run_id})

        total_savings = round(sum(r.get("estimated_savings_monthly", 0) for r in results), 2)
        total_actionable = sum(r.get("actionable_count", 0) for r in results)
        total_resources = sum(r.get("resource_count", 0) for r in results)

        digest_data = {
            "run_id": run_id,
            "subscription_id": subscription_id,
            "rg_results": results,
            "total_savings_monthly": total_savings,
            "total_actionable": total_actionable,
            "total_resources": total_resources,
            "rg_count": len(resource_groups),
        }

        yield context.call_activity("notify_user", {
            "user_id": "digest",
            "instance_id": run_id,
            "event": "daily_digest",
            "data": digest_data,
        })

        final = {
            "status": "completed",
            "run_id": run_id,
            "total_savings_monthly": total_savings,
            "total_actionable": total_actionable,
            "total_resources": total_resources,
            "rg_count": len(resource_groups),
        }
        context.set_custom_status({"stage": "completed", **final})
        return final

    return daily_digest_orchestrator
