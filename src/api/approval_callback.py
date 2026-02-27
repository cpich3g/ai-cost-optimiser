from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Any, Dict, Mapping

ALLOWED_DECISIONS = {
    "approve": "approve",
    "approved": "approve",
    "accept": "approve",
    "yes": "approve",
    "reject": "reject",
    "rejected": "reject",
    "deny": "reject",
    "no": "reject",
    "expire": "expire",
    "expired": "expire",
    "timeout": "expire",
}


@dataclass(frozen=True)
class ApprovalDecision:
    approval_id: str
    batch_id: str
    stage: str
    decision: str
    decided_at: str
    approver: str | None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approvalId": self.approval_id,
            "batchId": self.batch_id,
            "stage": self.stage,
            "decision": self.decision,
            "decidedAt": self.decided_at,
            "approver": self.approver,
        }


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_callback_guard(headers: Mapping[str, str], expected_secret: str | None = None) -> bool:
    expected = expected_secret or os.getenv("APPROVAL_CALLBACK_SECRET", "<your-secret-token>")
    if not expected:
        return False

    auth_header = headers.get("authorization") or headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        provided = auth_header.split(" ", 2)[1].strip()
        return provided == expected

    provided = (
        headers.get("x-approval-token")
        or headers.get("X-Approval-Token")
        or headers.get("x-approval-secret")
        or headers.get("X-Approval-Secret")
    )
    return provided == expected


def map_decision(raw_decision: str | None, deadline: str | None, now: datetime | None = None) -> str:
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    deadline_utc = _parse_utc(deadline)

    if raw_decision is None or str(raw_decision).strip() == "":
        if deadline_utc and now_utc > deadline_utc:
            return "expire"
        raise ValueError("decision is required before deadline")

    normalized = ALLOWED_DECISIONS.get(str(raw_decision).strip().lower())
    if not normalized:
        raise ValueError(f"unsupported decision '{raw_decision}'")

    if normalized in ("approve", "reject") and deadline_utc and now_utc > deadline_utc:
        return "expire"

    return normalized


def handle_approval_callback(
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    expected_secret: str | None = None,
    now: datetime | None = None,
) -> Dict[str, Any]:
    if not validate_callback_guard(headers, expected_secret=expected_secret):
        raise PermissionError("invalid callback credentials")

    required = ["approvalId", "batchId", "stage"]
    missing = [name for name in required if not payload.get(name)]
    if missing:
        raise ValueError(f"missing required fields: {', '.join(missing)}")

    decision = map_decision(payload.get("decision"), payload.get("deadline"), now=now)
    result = ApprovalDecision(
        approval_id=str(payload["approvalId"]),
        batch_id=str(payload["batchId"]),
        stage=str(payload["stage"]),
        decision=decision,
        decided_at=(now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        approver=(payload.get("approver") or payload.get("approverEmail")),
    )
    return result.to_dict()