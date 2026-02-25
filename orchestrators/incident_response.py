"""Durable orchestration for Azure Cost Optimiser with HITL approval."""

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
    recommendations: list[dict] = []
    for item in resources:
        resource_id = item.get("resource_id") or item.get("id") or "unknown-resource"
        cost = float(item.get("monthly_cost", 0) or 0)
        if cost >= 250:
            action_type = "delete"
            risk = "high"
            savings = round(cost * 0.7, 2)
            reason = "High recurring cost candidate."
        elif cost >= 100:
            action_type = "resize"
            risk = "medium"
            savings = round(cost * 0.35, 2)
            reason = "Likely oversized based on spend profile."
        else:
            action_type = "deallocate"
            risk = "low"
            savings = round(cost * 0.2, 2)
            reason = "Low activity candidate for deallocation."

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


def register_orchestrator(app):
    """Register orchestrator and execution activities to the function app."""

    @app.activity_trigger(input_name="action")
    def execute_cost_action(action: dict) -> dict:
        return {
            "resourceId": action.get("resourceId"),
            "actionType": action.get("actionType"),
            "status": "success",
            "details": "Executed in scaffold mode.",
        }

    @app.orchestration_trigger(context_name="context")
    def cost_optimization_orchestrator(context):
        input_data = context.get_input() or {}
        resources = input_data.get("resources", [])
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

        context.set_custom_status({"stage": "analyzing", "run_id": run_id})
        yield notify("analysis_started", {"resourceCount": len(resources)})

        analyzer = app.get_agent(context, "CostOptimizerAnalyzer")
        analyzer_thread = analyzer.get_new_thread()
        analysis_prompt = (
            "Analyze Azure resources and propose cost actions with risk levels.\n"
            f"Resources:\n{json.dumps(resources)}\n\n"
            "Return JSON only: {\"recommendations\": [{\"actionType\":\"resize|deallocate|delete\","
            "\"resourceId\":\"...\",\"riskLevel\":\"low|medium|high|critical\","
            "\"estimatedSavingsMonthlyUsd\":123.45,\"reason\":\"...\"}]}"
        )
        analysis_response = yield analyzer.run(messages=analysis_prompt, thread=analyzer_thread)

        analysis_text = analysis_response.text if analysis_response else ""
        try:
            recommendations = _extract_json(analysis_text).get("recommendations", [])
        except (json.JSONDecodeError, TypeError, AttributeError):
            recommendations = _fallback_recommendations(resources)

        high_risk_actions = [
            item
            for item in recommendations
            if str(item.get("riskLevel", "")).lower() in ("high", "critical")
            or str(item.get("actionType", "")).lower() == "delete"
        ]
        auto_actions = [item for item in recommendations if item not in high_risk_actions]
        approved_actions = list(auto_actions)

        if high_risk_actions:
            context.set_custom_status(
                {
                    "stage": "awaiting_approval",
                    "run_id": run_id,
                    "highRiskActionCount": len(high_risk_actions),
                }
            )
            yield notify(
                "approval_required",
                {"actions": high_risk_actions, "timeoutHours": APPROVAL_TIMEOUT_HOURS},
            )

            approval_event = context.wait_for_external_event("ApprovalDecision")
            timeout_task = context.create_timer(
                context.current_utc_datetime + timedelta(hours=APPROVAL_TIMEOUT_HOURS)
            )
            winner = yield context.task_any([approval_event, timeout_task])

            if winner == timeout_task:
                context.set_custom_status({"stage": "expired", "run_id": run_id})
                yield notify("approval_expired", {"run_id": run_id})
                return {"status": "expired", "run_id": run_id}

            timeout_task.cancel()
            approval_data = approval_event.result if isinstance(approval_event.result, dict) else {}
            if approval_data.get("decision") != "approve":
                context.set_custom_status({"stage": "rejected", "run_id": run_id})
                yield notify("approval_rejected", {"run_id": run_id})
                return {"status": "rejected", "run_id": run_id}

            approved_actions.extend(high_risk_actions)

        context.set_custom_status({"stage": "executing", "run_id": run_id})
        results = []
        for index, action in enumerate(approved_actions, start=1):
            yield notify(
                "action_started",
                {"index": index, "total": len(approved_actions), "action": action},
            )
            result = yield context.call_activity("execute_cost_action", action)
            results.append(result)
            yield notify(
                "action_completed",
                {"index": index, "total": len(approved_actions), "result": result},
            )

        total_savings = round(
            sum(float(item.get("estimatedSavingsMonthlyUsd", 0) or 0) for item in approved_actions),
            2,
        )
        final_result = {
            "status": "completed",
            "run_id": run_id,
            "recommendationCount": len(recommendations),
            "approvedActionCount": len(approved_actions),
            "estimatedSavingsMonthlyUsd": total_savings,
            "results": results,
        }
        context.set_custom_status({"stage": "completed", **final_result})
        yield notify("orchestration_completed", final_result)
        return final_result

    return cost_optimization_orchestrator
