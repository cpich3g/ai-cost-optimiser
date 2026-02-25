import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from common.storage import InMemoryIdempotencyStore
from orchestrator.idempotency import IdempotencyManager, deterministic_idempotency_key


def test_deterministic_idempotency_key_stable_for_equivalent_dicts():
    left = {"run": 1, "items": [{"a": 1, "b": 2}]}
    right = {"items": [{"b": 2, "a": 1}], "run": 1}

    assert deterministic_idempotency_key(left) == deterministic_idempotency_key(right)


def test_idempotency_manager_blocks_duplicate_begin_calls():
    manager = IdempotencyManager(InMemoryIdempotencyStore())
    key = deterministic_idempotency_key("same-request")

    assert manager.begin(key) is True
    assert manager.begin(key) is False