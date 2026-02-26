"""Durable orchestration for Azure Cost Optimiser with two-stage HITL approval.

Flow: Analysis → Engineering Approval → Finance Approval → Dry Run → Doc Lookup → Summary Email
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

from config import APPROVAL_TIMEOUT_HOURS


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", stripped, re.DOTALL)
    if fence_match:
        return json.loads(fence_match.group(1).strip())

    object_match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if object_match:
        return json.loads(object_match.group(0))

    raise json.JSONDecodeError("No JSON object found", stripped, 0)


def _fallback_recommendations(resources: list[dict]) -> list[dict]:
    """Heuristic fallback when the analyzer agent fails to return valid JSON.

    Never recommends 'delete' because we lack activity/usage data to confirm
    60-day inactivity. Sticks to resize and deallocate which are reversible.
    """
    recommendations: list[dict] = []
    for item in resources:
        resource_id = item.get("resource_id") or item.get("id") or "unknown-resource"
        cost = float(item.get("monthly_cost", 0) or 0)
        if cost >= 200:
            action_type = "resize"
            risk = "medium"
            savings = round(cost * 0.35, 2)
            reason = "High recurring cost — likely oversized. Review SKU/tier."
        elif cost >= 50:
            action_type = "resize"
            risk = "low"
            savings = round(cost * 0.2, 2)
            reason = "Moderate spend — may benefit from right-sizing."
        else:
            action_type = "no action needed"
            risk = "low"
            savings = 0
            reason = "Low cost — no optimisation needed."

        recommendations.append(
            {
                "actionType": action_type,
                "resourceId": resource_id,
                "riskLevel": risk,
                "estimatedSavingsMonthlyUsd": savings,
                "reason": reason,
            }
        )
    return recommendations


def _wait_for_decision(context, event_name: str):
    """Yield a (decision_data, timed_out) tuple from external event or timeout."""
    approval_event = context.wait_for_external_event(event_name)
    timeout_task = context.create_timer(
        context.current_utc_datetime + timedelta(hours=APPROVAL_TIMEOUT_HOURS)
    )
    winner = yield context.task_any([approval_event, timeout_task])

    if winner == timeout_task:
        return None  # timed out

    timeout_task.cancel()
    raw_result = approval_event.result
    if isinstance(raw_result, str):
        try:
            return json.loads(raw_result)
        except (json.JSONDecodeError, TypeError):
            return {"decision": raw_result}
    elif isinstance(raw_result, dict):
        return raw_result
    return {}


def register_orchestrator(app):
    """Register orchestrator and execution activities to the function app."""

    @app.activity_trigger(input_name="action")
    def execute_cost_action(action: dict) -> dict:
        """Dry-run execution — logs intent without modifying Azure resources."""
        action_type = action.get("actionType", "unknown")
        resource_id = action.get("resourceId", "")
        resource_name = resource_id.rsplit("/", 1)[-1] if "/" in resource_id else resource_id
        return {
            "resourceId": resource_id,
            "resourceName": resource_name,
            "actionType": action_type,
            "status": "dry_run",
            "details": f"[DRY RUN] Would {action_type} {resource_name}. No changes made.",
        }

    @app.orchestration_trigger(context_name="context")
    def cost_optimization_orchestrator(context):
        input_data = context.get_input() or {}
        resources = input_data.get("resources", [])
        resource_group = input_data.get("resource_group", "unknown")
        run_id = input_data.get("run_id", context.instance_id)
        user_id = input_data.get("user_id", "operator")

        def notify(event: str, data: dict):
            return context.call_activity(
                "notify_user",
                {
                    "user_id": user_id,
                    "instance_id": context.instance_id,
                    "event": event,
                    "data": data,
                },
            )

        # --- Stage 1: Analysis ---
        # Use pre-computed analysis from HTTP endpoint (MCP tools can't run
        # inside durable context due to Content serialization bug).
        context.set_custom_status({"stage": "analyzing", "run_id": run_id})
        yield notify("analysis_started", {"resourceCount": len(resources)})

        pre_analysis = input_data.get("agent_analysis", "")
        recommendations = []
        if pre_analysis:
            try:
                recommendations = _extract_json(pre_analysis).get("recommendations", [])
            except (json.JSONDecodeError, TypeError, AttributeError):
                recommendations = []

        if not recommendations:
            recommendations = _fallback_recommendations(resources)

        # Filter out "no action needed" for approval flow
        actionable = [
            r for r in recommendations
            if str(r.get("actionType", "")).lower() not in ("no action needed", "none", "")
        ]
        if not actionable:
            context.set_custom_status({"stage": "completed", "run_id": run_id})
            result = {
                "status": "completed",
                "run_id": run_id,
                "message": "No actionable cost optimisations found.",
                "recommendationCount": len(recommendations),
            }
            yield notify("orchestration_completed", result)
            return result

        total_savings = round(
            sum(float(a.get("estimatedSavingsMonthlyUsd", 0) or 0) for a in actionable), 2
        )

        # --- Stage 2: Engineering Approval ---
        context.set_custom_status({
            "stage": "awaiting_engineering",
            "run_id": run_id,
            "actionCount": len(actionable),
        })
        yield notify(
            "approval_required",
            {
                "actions": actionable,
                "allRecommendations": recommendations,
                "resourceGroup": resource_group,
                "stage": "engineering",
                "timeoutHours": APPROVAL_TIMEOUT_HOURS,
            },
        )

        eng_decision = yield from _wait_for_decision(context, "EngineeringDecision")
        if eng_decision is None:
            context.set_custom_status({"stage": "expired", "run_id": run_id})
            yield notify("approval_expired", {"run_id": run_id, "stage": "engineering"})
            return {"status": "expired", "run_id": run_id, "stage": "engineering"}
        if eng_decision.get("decision") != "approve":
            context.set_custom_status({"stage": "rejected", "run_id": run_id})
            yield notify("approval_rejected", {"run_id": run_id, "stage": "engineering"})
            return {"status": "rejected", "run_id": run_id, "stage": "engineering"}

        # --- Stage 3: Finance Approval ---
        context.set_custom_status({
            "stage": "awaiting_finance",
            "run_id": run_id,
            "estimatedSavingsMonthlyUsd": total_savings,
        })
        yield notify(
            "approval_required",
            {
                "actions": actionable,
                "allRecommendations": recommendations,
                "resourceGroup": resource_group,
                "stage": "finance",
                "timeoutHours": APPROVAL_TIMEOUT_HOURS,
            },
        )

        fin_decision = yield from _wait_for_decision(context, "FinanceDecision")
        if fin_decision is None:
            context.set_custom_status({"stage": "expired", "run_id": run_id})
            yield notify("approval_expired", {"run_id": run_id, "stage": "finance"})
            return {"status": "expired", "run_id": run_id, "stage": "finance"}
        if fin_decision.get("decision") != "approve":
            context.set_custom_status({"stage": "rejected", "run_id": run_id})
            yield notify("approval_rejected", {"run_id": run_id, "stage": "finance"})
            return {"status": "rejected", "run_id": run_id, "stage": "finance"}

        # --- Stage 4: Dry Run Execution ---
        context.set_custom_status({"stage": "executing_dry_run", "run_id": run_id})
        results = []
        for index, action in enumerate(actionable, start=1):
            yield notify(
                "action_started",
                {"index": index, "total": len(actionable), "action": action},
            )
            result = yield context.call_activity("execute_cost_action", action)
            results.append(result)
            yield notify(
                "action_completed",
                {"index": index, "total": len(actionable), "result": result},
            )

        # --- Stage 5: Doc Lookup via MS Learn MCP ---
        context.set_custom_status({"stage": "researching_docs", "run_id": run_id})
        doc_links = []
        try:
            doc_agent = app.get_agent(context, "DocResearchAgent")
            doc_thread = doc_agent.get_new_thread()
            action_types_seen = list({a.get("actionType", "") for a in actionable})
            resource_types_seen = list({
                a.get("resourceId", "").split("/providers/")[-1].split("/")[0] + "/" +
                a.get("resourceId", "").split("/providers/")[-1].split("/")[1]
                for a in actionable
                if len(a.get("resourceId", "").split("/providers/")) > 1
                and len(a.get("resourceId", "").split("/providers/")[-1].split("/")) >= 2
            })
            doc_prompt = (
                "Find relevant Microsoft Learn documentation links for Azure cost optimisation.\n"
                f"Action types performed: {json.dumps(action_types_seen)}\n"
                f"Azure resource types involved: {json.dumps(resource_types_seen)}\n\n"
                "Return JSON only: {\"docs\": [{\"title\":\"...\",\"url\":\"https://learn.microsoft.com/...\","
                "\"relevance\":\"...\"}]}\n"
                "Include docs for: best practices for each action type, resource-specific sizing guides, "
                "Azure cost management overview, and Azure Advisor recommendations."
            )
            doc_response = yield doc_agent.run(messages=doc_prompt, thread=doc_thread)
            doc_text = doc_response.text if doc_response else ""
            doc_links = _extract_json(doc_text).get("docs", [])
        except Exception:
            # Fallback: agent knowledge without MCP is still useful
            doc_links = []

        # --- Stage 6: Summary Email to Both ---
        context.set_custom_status({"stage": "sending_summary", "run_id": run_id})
        final_result = {
            "status": "completed",
            "run_id": run_id,
            "resourceGroup": resource_group,
            "recommendationCount": len(recommendations),
            "actionableCount": len(actionable),
            "estimatedSavingsMonthlyUsd": total_savings,
            "results": results,
            "docLinks": doc_links,
        }
        yield notify("execution_summary", final_result)

        context.set_custom_status({"stage": "completed", **final_result})
        yield notify("orchestration_completed", final_result)
        return final_result

    return cost_optimization_orchestrator
