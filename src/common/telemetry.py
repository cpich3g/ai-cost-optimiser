from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping


@dataclass
class InMemoryTelemetryCollector:
    """Minimal telemetry sink for tests and local scaffold runs."""

    events: List[Dict[str, Any]] = field(default_factory=list)

    def emit(self, name: str, attributes: Mapping[str, Any] | None = None) -> Dict[str, Any]:
        event = {
            "name": name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attributes": dict(attributes or {}),
        }
        self.events.append(event)
        return event