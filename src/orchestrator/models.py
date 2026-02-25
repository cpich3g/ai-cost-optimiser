from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionType(str, Enum):
    RESIZE = "resize"
    DEALLOCATE = "deallocate"
    DELETE = "delete"


@dataclass(frozen=True)
class CandidateResource:
    resource_id: str
    resource_type: str
    monthly_cost: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionPlan:
    resource_id: str
    action_type: ActionType
    risk_level: RiskLevel
    reason: str
    estimated_monthly_savings: float


@dataclass(frozen=True)
class ActionBatch:
    batch_id: str
    risk_level: RiskLevel
    actions: List[ActionPlan]
    requires_approval: bool


@dataclass(frozen=True)
class DispatchResult:
    resource_id: str
    action_type: ActionType
    succeeded: bool
    message: str


@dataclass(frozen=True)
class VerificationResult:
    resource_id: str
    verified: bool
    message: str


@dataclass(frozen=True)
class CompensationResult:
    resource_id: str
    compensated: bool
    message: str


@dataclass
class OrchestrationResult:
    run_id: str
    idempotency_key: str
    status: str
    approvals: List[Dict[str, Any]] = field(default_factory=list)
    dispatches: List[DispatchResult] = field(default_factory=list)
    verifications: List[VerificationResult] = field(default_factory=list)
    compensations: List[CompensationResult] = field(default_factory=list)