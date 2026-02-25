from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping
import uuid

from common.storage import InMemoryIdempotencyStore, InMemoryStateStore
from common.telemetry import InMemoryTelemetryCollector
from executor.compensation import compensate_action
from executor.verification import verify_action
from executor.worker import execute_action
from orchestrator.activities.classify_and_batch import classify_and_batch
from orchestrator.activities.dispatch_actions import dispatch_actions_sequentially
from orchestrator.activities.request_approval import build_approval_request_payload
from orchestrator.activities.scan_candidates import scan_candidates
from orchestrator.idempotency import IdempotencyManager, deterministic_idempotency_key
from orchestrator.models import (
    ActionBatch,
    CompensationResult,
    DispatchResult,
    OrchestrationResult,
    VerificationResult,
)

ApprovalHook = Callable[[Mapping[str, Any]], Dict[str, Any]]


def _risk_score(level: str) -> float:
    return {
        "low": 0.2,
        "medium": 0.5,
        "high": 0.8,
        "critical": 0.95,
    }.get(level, 0.5)


def _default_approval_hook(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "approvalId": payload["approvalId"],
        "batchId": payload["batchId"],
        "stage": payload["stage"],
        "decision": "approve",
    }


def _approval_handoff(batch: ActionBatch, approval_hook: ApprovalHook) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    reasons = sorted({action.reason for action in batch.actions})

    for stage in ("finance", "engineering"):
        payload = build_approval_request_payload(
            batch_id=batch.batch_id,
            stage=stage,
            risk_level=batch.risk_level.value,
            risk_score=_risk_score(batch.risk_level.value),
            reasons=reasons,
            deadline=datetime.now(timezone.utc) + timedelta(hours=24),
            approval_id=str(uuid.uuid4()),
        )
        decision = approval_hook(payload)
        records.append({"request": payload, "decision": dict(decision)})
        if decision.get("decision") != "approve":
            break

    return records


def run_orchestrator(
    *,
    run_id: str,
    resources: Iterable[Mapping[str, object]],
    idempotency_store: InMemoryIdempotencyStore | None = None,
    state_store: InMemoryStateStore | None = None,
    approval_hook: ApprovalHook | None = None,
    verification_hook: Callable[[DispatchResult], VerificationResult] = verify_action,
    compensation_hook: Callable[[DispatchResult, VerificationResult], CompensationResult] = compensate_action,
    telemetry: InMemoryTelemetryCollector | None = None,
) -> OrchestrationResult:
    telemetry = telemetry or InMemoryTelemetryCollector()
    state_store = state_store or InMemoryStateStore()

    resources_for_key = [dict(item) for item in resources]
    idempotency_key = deterministic_idempotency_key(run_id, resources_for_key)
    manager = IdempotencyManager(idempotency_store)

    if not manager.begin(idempotency_key):
        previous = manager.get_result(idempotency_key)
        if isinstance(previous, OrchestrationResult):
            return previous
        return OrchestrationResult(run_id=run_id, idempotency_key=idempotency_key, status="duplicate")

    telemetry.emit("orchestrator.start", {"runId": run_id})

    candidates = scan_candidates(resources_for_key)
    batches = classify_and_batch(candidates)

    all_approvals: List[Dict[str, Any]] = []
    all_dispatches: List[DispatchResult] = []
    all_verifications: List[VerificationResult] = []
    all_compensations: List[CompensationResult] = []

    active_approval_hook = approval_hook or _default_approval_hook

    for batch in batches:
        approved = True
        if batch.requires_approval:
            approvals = _approval_handoff(batch, active_approval_hook)
            all_approvals.extend(approvals)
            approved = approvals and approvals[-1]["decision"].get("decision") == "approve"

        if not approved:
            telemetry.emit("orchestrator.batch.skipped", {"batchId": batch.batch_id, "reason": "approval_denied"})
            continue

        dispatches = dispatch_actions_sequentially(batch.actions, action_executor=execute_action)
        all_dispatches.extend(dispatches)

        for dispatch in dispatches:
            verification = verification_hook(dispatch)
            all_verifications.append(verification)

            compensation = compensation_hook(dispatch, verification)
            if compensation.compensated:
                all_compensations.append(compensation)

    status = "completed"
    result = OrchestrationResult(
        run_id=run_id,
        idempotency_key=idempotency_key,
        status=status,
        approvals=all_approvals,
        dispatches=all_dispatches,
        verifications=all_verifications,
        compensations=all_compensations,
    )

    state_store.create_or_update(
        run_id,
        {
            "run_id": run_id,
            "status": status,
            "idempotency_key": idempotency_key,
            "dispatch_count": len(all_dispatches),
        },
    )
    manager.remember_result(idempotency_key, result)
    telemetry.emit("orchestrator.completed", {"runId": run_id, "dispatchCount": len(all_dispatches)})

    return result