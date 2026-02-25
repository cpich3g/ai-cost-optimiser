from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from common.storage import InMemoryIdempotencyStore


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _normalize(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def deterministic_idempotency_key(*parts: Any, namespace: str = "core-orchestrator") -> str:
    normalized_parts = [_normalize(part) for part in parts]
    payload = json.dumps(normalized_parts, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(f"{namespace}:{payload}".encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"


class IdempotencyManager:
    def __init__(self, store: InMemoryIdempotencyStore | None = None) -> None:
        self.store = store or InMemoryIdempotencyStore()

    def begin(self, key: str) -> bool:
        return self.store.remember(key)

    def remember_result(self, key: str, result: Any) -> None:
        self.store.set(key, result)

    def get_result(self, key: str) -> Any:
        return self.store.get(key)