from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from orchestrator.models import CandidateResource


def scan_candidates(resources: Iterable[Mapping[str, object] | CandidateResource]) -> Sequence[CandidateResource]:
    """Converts raw resources into typed candidates for downstream activities."""
    candidates = []
    for item in resources:
        if isinstance(item, CandidateResource):
            candidates.append(item)
            continue

        resource_id = str(item.get("resource_id") or item.get("id") or "")
        if not resource_id:
            continue

        resource_type = str(item.get("resource_type") or item.get("type") or "unknown")
        monthly_cost = float(item.get("monthly_cost") or 0.0)
        metadata = dict(item.get("metadata") or {})
        if monthly_cost <= 0:
            continue

        candidates.append(
            CandidateResource(
                resource_id=resource_id,
                resource_type=resource_type,
                monthly_cost=monthly_cost,
                metadata=metadata,
            )
        )

    return candidates