"""Runtime orchestrator adapters and shared utilities."""

import json
import re


def extract_json(text: str) -> dict:
    """Extract JSON from text, handling markdown code fences and plain objects.

    Attempts to parse:
    1. Plain JSON
    2. JSON within markdown code fences (```json)
    3. First JSON object found in text
    """
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Try to extract from markdown code fence
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", stripped, re.DOTALL)
    if fence_match:
        return json.loads(fence_match.group(1).strip())

    # Try to find first JSON object
    object_match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if object_match:
        return json.loads(object_match.group(0))

    raise json.JSONDecodeError("No JSON object found", stripped, 0)
