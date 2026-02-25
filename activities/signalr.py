"""SignalR activity adapter for orchestration notifications.

The deployment request targets Function App + Logic App only; this adapter keeps
notifications safe even when SignalR isn't provisioned.
"""

from __future__ import annotations


def register_signalr_activities(app, _hub_name: str, _connection_setting: str):
    """Register a lightweight notify activity used by orchestrations."""

    @app.activity_trigger(input_name="payload")
    def notify_user(payload: dict) -> dict:
        return {
            "sent": False,
            "reason": "signalr_not_deployed_in_this_profile",
            "payload": payload,
        }

    return notify_user
