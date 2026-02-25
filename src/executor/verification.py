from __future__ import annotations

from orchestrator.models import DispatchResult, VerificationResult


def verify_action(dispatch: DispatchResult) -> VerificationResult:
    if not dispatch.succeeded:
        return VerificationResult(
            resource_id=dispatch.resource_id,
            verified=False,
            message="Dispatch failed, verification failed",
        )

    if dispatch.resource_id.startswith("unverified-"):
        return VerificationResult(
            resource_id=dispatch.resource_id,
            verified=False,
            message="Post-action check failed",
        )

    return VerificationResult(
        resource_id=dispatch.resource_id,
        verified=True,
        message="Verification passed",
    )