from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List

from orchestrator.models import ActionBatch, ActionPlan, ActionType, CandidateResource, RiskLevel


def classify_candidate(candidate: CandidateResource) -> ActionPlan:
    if candidate.monthly_cost >= 250:
        action_type = ActionType.DELETE
        risk = RiskLevel.HIGH
        reason = "High monthly cost candidate for delete"
    elif candidate.monthly_cost >= 100:
        action_type = ActionType.RESIZE
        risk = RiskLevel.MEDIUM
        reason = "Moderate cost candidate for resize"
    else:
        action_type = ActionType.DEALLOCATE
        risk = RiskLevel.LOW
        reason = "Low cost idle candidate for deallocate"

    if candidate.metadata.get("production") is True:
        risk = RiskLevel.CRITICAL
        if action_type == ActionType.DELETE:
            action_type = ActionType.RESIZE
            reason = "Production resource downgraded from delete to resize"

    return ActionPlan(
        resource_id=candidate.resource_id,
        action_type=action_type,
        risk_level=risk,
        reason=reason,
        estimated_monthly_savings=round(candidate.monthly_cost * 0.4, 2),
    )


def classify_and_batch(candidates: Iterable[CandidateResource], batch_size: int = 2) -> List[ActionBatch]:
    grouped: Dict[RiskLevel, List[ActionPlan]] = defaultdict(list)
    for candidate in candidates:
        plan = classify_candidate(candidate)
        grouped[plan.risk_level].append(plan)

    batches: List[ActionBatch] = []
    for risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL):
        items = grouped.get(risk_level, [])
        if not items:
            continue

        for idx in range(0, len(items), batch_size):
            actions = items[idx : idx + batch_size]
            batch_index = idx // batch_size + 1
            batch_id = f"batch-{risk_level.value}-{batch_index}"
            requires_approval = risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL) or any(
                action.action_type == ActionType.DELETE for action in actions
            )
            batches.append(
                ActionBatch(
                    batch_id=batch_id,
                    risk_level=risk_level,
                    actions=actions,
                    requires_approval=requires_approval,
                )
            )

    return batches