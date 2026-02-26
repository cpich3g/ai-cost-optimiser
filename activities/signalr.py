"""Notification activity for orchestration events.

Calls the Logic App HTTP trigger to send approval emails via O365 Outlook
when the orchestrator reaches the approval gate.
"""

from __future__ import annotations

import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)


def register_signalr_activities(app, _hub_name: str, _connection_setting: str):
    """Register a notify activity that triggers the Logic App for approval emails."""

    @app.activity_trigger(input_name="payload")
    def notify_user(payload: dict) -> dict:
        event = payload.get("event", "")
        instance_id = payload.get("instance_id", "")
        data = payload.get("data", {})

        logicapp_url = os.getenv("LOGICAPP_TRIGGER_URL", "").strip()

        # Only fire the Logic App for approval-related events
        if event == "approval_required" and logicapp_url:
            actions = data.get("actions", [])
            timeout_hours = data.get("timeoutHours", 24)

            # Build approval request body matching the Logic App schema
            func_base = os.getenv(
                "WEBSITE_HOSTNAME",
                "func-cost-optimiser-flex8029.azurewebsites.net",
            )
            approval_id = f"approval-{instance_id}"
            from datetime import datetime, timedelta, timezone
            deadline = (datetime.now(timezone.utc) + timedelta(hours=timeout_hours)).isoformat()
            body = {
                "approvalId": approval_id,
                "batchId": instance_id,
                "instanceId": instance_id,
                "stage": "finance",
                "deadline": deadline,
                "riskSummary": {
                    "level": "high" if len(actions) > 3 else "medium",
                    "score": round(len(actions) / 10, 2),
                    "details": f"{len(actions)} high-risk actions require approval",
                },
                "actions": actions[:20],
                "callbackUrl": f"https://{func_base}/api/cost-optimization/{instance_id}/decide",
                "estimatedSavings": round(
                    sum(float(a.get("estimatedSavingsMonthlyUsd", 0) or 0) for a in actions), 2
                ),
            }

            try:
                resp = httpx.post(
                    logicapp_url,
                    json=body,
                    timeout=30,
                )
                logger.info(
                    "Logic App triggered for %s: HTTP %s",
                    instance_id,
                    resp.status_code,
                )
                return {
                    "sent": True,
                    "channel": "logicapp_email",
                    "event": event,
                    "logicapp_status": resp.status_code,
                    "approval_id": approval_id,
                }
            except Exception as exc:
                logger.warning("Failed to trigger Logic App: %s", exc)
                return {
                    "sent": False,
                    "reason": f"logicapp_trigger_failed: {exc}",
                    "event": event,
                }

        # For non-approval events, just log
        logger.info("notify_user event=%s instance=%s", event, instance_id)
        return {
            "sent": False,
            "reason": "no_logicapp_for_event" if not logicapp_url else "non_approval_event",
            "event": event,
            "payload": payload,
        }

    return notify_user
