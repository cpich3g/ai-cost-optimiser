from __future__ import annotations

from orchestrator.models import ActionPlan, DispatchResult


def execute_action(action: ActionPlan) -> DispatchResult:
    """Stub executor that simulates dispatch success/failure deterministically."""
    if action.resource_id.startswith("fail-dispatch-"):
        return DispatchResult(
            resource_id=action.resource_id,
            action_type=action.action_type,
            succeeded=False,
            message="Dispatch failed in scaffold executor",
        )

    return DispatchResult(
        resource_id=action.resource_id,
        action_type=action.action_type,
        succeeded=True,
        message=f"Executed {action.action_type.value}",
    )