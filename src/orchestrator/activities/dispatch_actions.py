from __future__ import annotations

from typing import Callable, Iterable, List

from orchestrator.models import ActionPlan, DispatchResult


def dispatch_actions_sequentially(
    actions: Iterable[ActionPlan],
    action_executor: Callable[[ActionPlan], DispatchResult],
) -> List[DispatchResult]:
    """Dispatches actions in-order, one-by-one."""
    results: List[DispatchResult] = []
    for action in actions:
        results.append(action_executor(action))
    return results