from __future__ import annotations

from orchestrator.models import CompensationResult, DispatchResult, VerificationResult


def compensate_action(dispatch: DispatchResult, verification: VerificationResult) -> CompensationResult:
    if dispatch.succeeded and not verification.verified:
        return CompensationResult(
            resource_id=dispatch.resource_id,
            compensated=True,
            message="Compensation initiated due to failed verification",
        )

    return CompensationResult(
        resource_id=dispatch.resource_id,
        compensated=False,
        message="No compensation required",
    )