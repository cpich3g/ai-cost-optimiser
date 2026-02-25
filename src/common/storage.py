from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, Optional


class InMemoryIdempotencyStore:
    """Thread-safe in-memory dedupe/result store scaffold."""

    def __init__(self) -> None:
        self._records: Dict[str, Any] = {}
        self._lock = Lock()

    def seen(self, key: str) -> bool:
        with self._lock:
            return key in self._records

    def remember(self, key: str, value: Any = None) -> bool:
        """Returns True when key is first seen; False when duplicate."""
        with self._lock:
            if key in self._records:
                return False
            self._records[key] = value
            return True

    def get(self, key: str) -> Any:
        with self._lock:
            return self._records.get(key)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._records[key] = value


@dataclass
class InMemoryStateStore:
    """Simple run-state store keyed by orchestrator run id."""

    _runs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def create_or_update(self, run_id: str, state: Dict[str, Any]) -> None:
        with self._lock:
            self._runs[run_id] = dict(state)

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            value = self._runs.get(run_id)
            return dict(value) if value is not None else None