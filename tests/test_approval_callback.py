from datetime import datetime, timedelta, timezone
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from api.approval_callback import handle_approval_callback, map_decision, validate_callback_guard


SECRET = "unit-test-secret"


def _valid_payload(**overrides):
    payload = {
        "approvalId": "apr-123",
        "batchId": "batch-999",
        "stage": "finance",
        "deadline": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "decision": "approve",
        "approverEmail": "approver@contoso.com",
    }
    payload.update(overrides)
    return payload


def test_validate_callback_guard_accepts_bearer_and_header_token():
    assert validate_callback_guard({"Authorization": f"Bearer {SECRET}"}, expected_secret=SECRET)
    assert validate_callback_guard({"x-approval-token": SECRET}, expected_secret=SECRET)


def test_handle_callback_rejects_invalid_secret():
    with pytest.raises(PermissionError):
        handle_approval_callback(
            _valid_payload(),
            {"x-approval-token": "wrong"},
            expected_secret=SECRET,
        )


def test_map_decision_expire_if_deadline_passed():
    deadline = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assert map_decision(None, deadline) == "expire"
    assert map_decision("approve", deadline) == "expire"


def test_map_decision_rejects_unsupported_value():
    with pytest.raises(ValueError):
        map_decision("maybe", None)


def test_handle_callback_maps_and_returns_decision():
    result = handle_approval_callback(
        _valid_payload(decision="approved"),
        {"x-approval-token": SECRET},
        expected_secret=SECRET,
    )

    assert result["approvalId"] == "apr-123"
    assert result["batchId"] == "batch-999"
    assert result["stage"] == "finance"
    assert result["decision"] == "approve"
    assert result["approver"] == "approver@contoso.com"