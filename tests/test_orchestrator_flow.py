from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from common.storage import InMemoryIdempotencyStore
from orchestrator.function_app import run_orchestrator


def test_orchestrator_flow_with_approval_and_compensation_hooks():
    approvals_seen = []

    def approval_hook(payload):
        approvals_seen.append(payload)
        return {
            "approvalId": payload["approvalId"],
            "batchId": payload["batchId"],
            "stage": payload["stage"],
            "decision": "approve",
        }

    resources = [
        {"resource_id": "vm-low-1", "resource_type": "vm", "monthly_cost": 20.0},
        {"resource_id": "unverified-vm-2", "resource_type": "vm", "monthly_cost": 120.0},
        {"resource_id": "vm-high-3", "resource_type": "vm", "monthly_cost": 350.0},
    ]

    result = run_orchestrator(
        run_id="run-001",
        resources=resources,
        idempotency_store=InMemoryIdempotencyStore(),
        approval_hook=approval_hook,
    )

    assert result.status == "completed"
    assert len(result.dispatches) == 3
    assert len(approvals_seen) == 2
    assert {item["stage"] for item in approvals_seen} == {"finance", "engineering"}
    assert any(not verification.verified for verification in result.verifications)
    assert len(result.compensations) == 1


def test_orchestrator_idempotent_duplicate_returns_previous_result():
    store = InMemoryIdempotencyStore()
    resources = [{"resource_id": "vm-low-1", "resource_type": "vm", "monthly_cost": 20.0}]

    first = run_orchestrator(run_id="run-dup", resources=resources, idempotency_store=store)
    second = run_orchestrator(run_id="run-dup", resources=resources, idempotency_store=store)

    assert first.idempotency_key == second.idempotency_key
    assert first.status == second.status == "completed"
    assert len(second.dispatches) == 1