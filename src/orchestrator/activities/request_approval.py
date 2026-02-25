from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal
import uuid

ApprovalStage = Literal["finance", "engineering"]


@dataclass(frozen=True)
class RiskSummary:
    """Compact risk context included in approval emails and callbacks."""

    level: str
    score: float
    reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "score": self.score,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ApprovalRequestPayload:
    """Canonical payload contract for Logic App/O365 approval requests."""

    approval_id: str
    batch_id: str
    stage: ApprovalStage
    deadline: str
    risk_summary: RiskSummary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approvalId": self.approval_id,
            "batchId": self.batch_id,
            "stage": self.stage,
            "deadline": self.deadline,
            "riskSummary": self.risk_summary.to_dict(),
        }


def build_approval_request_payload(
    *,
    batch_id: str,
    stage: ApprovalStage,
    risk_level: str,
    risk_score: float,
    reasons: List[str],
    deadline: datetime,
    approval_id: str | None = None,
) -> Dict[str, Any]:
    """Build and validate approval payload used by the orchestrator activity scaffold."""

    if not batch_id:
        raise ValueError("batch_id is required")

    if stage not in ("finance", "engineering"):
        raise ValueError("stage must be either 'finance' or 'engineering'")

    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)

    payload = ApprovalRequestPayload(
        approval_id=approval_id or str(uuid.uuid4()),
        batch_id=batch_id,
        stage=stage,
        deadline=deadline.astimezone(timezone.utc).isoformat(),
        risk_summary=RiskSummary(
            level=risk_level,
            score=float(risk_score),
            reasons=reasons or [],
        ),
    )
    return payload.to_dict()