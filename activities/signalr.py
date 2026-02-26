"""Notification activity for orchestration events.

Calls the Logic App HTTP trigger (generic email relay) to send approval
and summary emails via O365 Outlook.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared HTML helpers
# ---------------------------------------------------------------------------

_RISK_COLORS = {
    "critical": "#dc2626", "high": "#ef4444",
    "medium": "#f59e0b", "low": "#10b981",
}
_ACTION_ICONS = {
    "delete": "🗑️", "deallocate": "⏸️",
    "resize": "📐", "no action needed": "✅",
}


def _resource_short(resource_id: str) -> tuple[str, str]:
    """Return (resource_name, resource_type) from a full ARM ID."""
    name = resource_id.rsplit("/", 1)[-1] if "/" in resource_id else resource_id
    rtype = ""
    parts = resource_id.split("/providers/")
    if len(parts) > 1:
        tp = parts[-1].split("/")
        if len(tp) >= 2:
            rtype = f"{tp[0]}/{tp[1]}"
    return name, rtype


def _build_actions_table(actions: list[dict]) -> str:
    """Build an HTML table of proposed actions."""
    rows = ""
    for a in actions:
        risk = str(a.get("riskLevel", "medium")).lower()
        action_type = str(a.get("actionType", ""))
        name, rtype = _resource_short(a.get("resourceId", ""))
        icon = _ACTION_ICONS.get(action_type.lower(), "⚙️")
        color = _RISK_COLORS.get(risk, "#6b7280")
        savings = f"${a.get('estimatedSavingsMonthlyUsd', 0)}"
        reason = a.get("reason", "")
        rows += (
            f"<tr>"
            f"<td style='padding:10px 8px;border-bottom:1px solid #eee'>"
            f"<strong>{name}</strong><br>"
            f"<span style='font-size:11px;color:#888'>{rtype}</span></td>"
            f"<td style='padding:10px 8px;border-bottom:1px solid #eee;text-align:center'>"
            f"{icon} {action_type}</td>"
            f"<td style='padding:10px 8px;border-bottom:1px solid #eee;text-align:center'>"
            f"<span style='background:{color};color:#fff;padding:2px 8px;"
            f"border-radius:4px;font-size:12px;font-weight:600'>{risk}</span></td>"
            f"<td style='padding:10px 8px;border-bottom:1px solid #eee;"
            f"text-align:right;font-weight:600'>{savings}/mo</td>"
            f"<td style='padding:10px 8px;border-bottom:1px solid #eee;"
            f"font-size:12px;color:#555'>{reason}</td>"
            f"</tr>"
        )
    return (
        "<table style='width:100%;border-collapse:collapse;font-size:13px'>"
        "<thead><tr style='background:#f8f9fa;text-align:left'>"
        "<th style='padding:10px 8px;font-weight:600'>Resource</th>"
        "<th style='padding:10px 8px;font-weight:600;text-align:center'>Action</th>"
        "<th style='padding:10px 8px;font-weight:600;text-align:center'>Risk</th>"
        "<th style='padding:10px 8px;font-weight:600;text-align:right'>Savings</th>"
        "<th style='padding:10px 8px;font-weight:600'>Reason</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


def _wrap_email(header_title: str, header_subtitle: str, body_sections: str) -> str:
    """Wrap content in the standard email chrome."""
    return (
        "<html><body style='font-family:Segoe UI,sans-serif;color:#1a1a2e;"
        "max-width:720px;margin:0 auto;background:#fff'>"
        "<div style='background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);"
        "padding:32px;border-radius:12px 12px 0 0;color:#e0e0e0'>"
        f"<h1 style='margin:0 0 4px;font-size:22px;color:#fff'>{header_title}</h1>"
        f"<p style='margin:0;font-size:14px;opacity:0.8'>{header_subtitle}</p></div>"
        f"{body_sections}"
        "<div style='padding:12px 24px;background:#f8f9fa;border:1px solid #e5e7eb;"
        "border-top:none;border-radius:0 0 12px 12px'>"
        "<p style='font-size:11px;color:#888;margin:0;text-align:center'>"
        "Automated email from Azure Cost Optimisation Agent</p></div>"
        "</body></html>"
    )


def _section(html: str, *, bg: str = "#fff") -> str:
    return (
        f"<div style='padding:20px 24px;border:1px solid #e5e7eb;border-top:none;"
        f"background:{bg}'>{html}</div>"
    )


def _approve_reject_buttons(callback_url: str, key: str, *, stage: str = "finance") -> str:
    return (
        "<div style='text-align:center;padding:28px 24px;"
        "border:1px solid #e5e7eb;border-top:none'>"
        f"<a href=\"{callback_url}?decision=approve&amp;key={key}&amp;stage={stage}\" "
        "style='display:inline-block;padding:14px 44px;background:#10b981;"
        "color:#fff;text-decoration:none;border-radius:8px;font-weight:600;"
        "font-size:16px;margin:0 8px'>✅ Approve</a>"
        f"<a href=\"{callback_url}?decision=reject&amp;key={key}&amp;stage={stage}\" "
        "style='display:inline-block;padding:14px 44px;background:#ef4444;"
        "color:#fff;text-decoration:none;border-radius:8px;font-weight:600;"
        "font-size:16px;margin:0 8px'>❌ Reject</a></div>"
    )


def _summary_row(label: str, value: str, *, bold: bool = False, color: str = "") -> str:
    vs = f"font-weight:700;{'color:' + color + ';' if color else ''}" if bold else ""
    return (
        f"<tr><td style='padding:6px 12px;font-weight:600;width:180px;color:#555'>"
        f"{label}</td><td style='padding:6px 12px;{vs}'>{value}</td></tr>"
    )


# ---------------------------------------------------------------------------
# Email builders per event type
# ---------------------------------------------------------------------------

def _build_engineering_email(instance_id: str, data: dict) -> dict:
    """Engineering review email — full action plan with technical detail."""
    actions = data.get("actions", [])
    rg = data.get("resourceGroup", "unknown")
    deadline_str = (datetime.now(timezone.utc) + timedelta(
        hours=data.get("timeoutHours", 24)
    )).strftime("%Y-%m-%d %H:%M UTC")
    total_savings = round(
        sum(float(a.get("estimatedSavingsMonthlyUsd", 0) or 0) for a in actions), 2
    )
    func_base = os.getenv("WEBSITE_HOSTNAME", "func-cost-optimiser-flex8029.azurewebsites.net")
    key = os.getenv("APPROVAL_CALLBACK_SECRET", "dev-shared-secret")
    callback = f"https://{func_base}/api/cost-optimization/{instance_id}/email-decide"

    risk_counts = {}
    for a in actions:
        r = str(a.get("riskLevel", "medium")).lower()
        risk_counts[r] = risk_counts.get(r, 0) + 1
    risk_summary = ", ".join(f"{v} {k}" for k, v in sorted(risk_counts.items()))

    summary_table = (
        "<table style='width:100%;border-collapse:collapse;font-size:14px'>"
        + _summary_row("Resource Group", f"<code>{rg}</code>", bold=True)
        + _summary_row("Total Actions", str(len(actions)))
        + _summary_row("Risk Breakdown", risk_summary)
        + _summary_row("Est. Monthly Savings", f"${total_savings}", bold=True, color="#10b981")
        + _summary_row("Approval Deadline", deadline_str)
        + _summary_row("Batch ID", f"<code style='font-size:12px'>{instance_id}</code>")
        + "</table>"
    )

    context_html = (
        "<p><strong>🔧 Engineering Review Required</strong></p>"
        "<p>The cost optimisation agent has analysed resource group "
        f"<code>{rg}</code> and proposes the following changes. "
        "Please review each action for:</p>"
        "<ul style='margin:8px 0;padding-left:20px;font-size:13px;color:#555'>"
        "<li>Service dependencies — will any other resources break?</li>"
        "<li>Data loss risk — are backups in place for deletions?</li>"
        "<li>Performance impact — are resize targets adequate for workload?</li>"
        "<li>Rollback feasibility — can each action be reversed?</li></ul>"
        "<p style='font-size:13px;color:#555;background:#fff8e1;padding:10px 14px;"
        "border-left:4px solid #f59e0b;border-radius:4px;margin:12px 0'>"
        "⚠️ <strong>Deletion policy:</strong> Resources are only recommended for "
        "deletion when they show <strong>no activity in the past 60 days</strong> "
        "(e.g. unattached disks, orphaned NICs, idle Event Grid topics). "
        "Verify the inactivity claim in each action's reason before approving.</p>"
        "<p style='font-size:13px;color:#555'>After your approval, this plan "
        "will be sent to Finance for budget sign-off before any dry-run execution.</p>"
    )

    body = _wrap_email(
        "Azure Cost Optimisation",
        f"Engineering review for <strong>{rg}</strong>",
        _section(summary_table, bg="#f8f9fa")
        + _section(context_html)
        + _section(f"<h3 style='margin:0 0 12px;font-size:15px'>Proposed Action Plan</h3>"
                   f"{_build_actions_table(actions)}")
        + _approve_reject_buttons(callback, key, stage="engineering"),
    )

    return {
        "to": os.getenv("ENGINEERING_EMAIL", "justinjoy@microsoft.com"),
        "subject": f"🔧 Engineering Review — {len(actions)} actions for {rg} (${total_savings}/mo savings)",
        "htmlBody": body,
    }


def _build_finance_email(instance_id: str, data: dict) -> dict:
    """Finance approval email — savings-focused after engineering sign-off."""
    actions = data.get("actions", [])
    rg = data.get("resourceGroup", "unknown")
    deadline_str = (datetime.now(timezone.utc) + timedelta(
        hours=data.get("timeoutHours", 24)
    )).strftime("%Y-%m-%d %H:%M UTC")
    total_savings = round(
        sum(float(a.get("estimatedSavingsMonthlyUsd", 0) or 0) for a in actions), 2
    )
    annual_savings = round(total_savings * 12, 2)
    func_base = os.getenv("WEBSITE_HOSTNAME", "func-cost-optimiser-flex8029.azurewebsites.net")
    key = os.getenv("APPROVAL_CALLBACK_SECRET", "dev-shared-secret")
    callback = f"https://{func_base}/api/cost-optimization/{instance_id}/email-decide"

    # Group savings by action type
    by_type: dict[str, float] = {}
    for a in actions:
        t = a.get("actionType", "other")
        by_type[t] = by_type.get(t, 0) + float(a.get("estimatedSavingsMonthlyUsd", 0) or 0)
    type_breakdown = ", ".join(f"{t}: ${round(v, 2)}/mo" for t, v in sorted(by_type.items()))

    summary_table = (
        "<table style='width:100%;border-collapse:collapse;font-size:14px'>"
        + _summary_row("Resource Group", f"<code>{rg}</code>", bold=True)
        + _summary_row("Monthly Savings", f"${total_savings}", bold=True, color="#10b981")
        + _summary_row("Annual Savings", f"${annual_savings}", bold=True, color="#10b981")
        + _summary_row("Actions", str(len(actions)))
        + _summary_row("Savings by Type", type_breakdown)
        + _summary_row("Approval Deadline", deadline_str)
        + _summary_row("Batch ID", f"<code style='font-size:12px'>{instance_id}</code>")
        + "</table>"
    )

    context_html = (
        "<p><strong>💰 Finance Approval Required</strong></p>"
        "<p>Engineering has reviewed and approved the following cost optimisation "
        f"plan for resource group <code>{rg}</code>. This plan would save an estimated "
        f"<strong>${total_savings}/month (${annual_savings}/year)</strong>.</p>"
        "<p style='font-size:13px;color:#555'>Upon your approval, the agent will "
        "execute a <strong>dry run</strong> — simulating each action without making "
        "actual changes to Azure resources. A summary report with Microsoft Learn "
        "documentation links will be sent to both engineering and finance.</p>"
    )

    body = _wrap_email(
        "Azure Cost Optimisation",
        f"Finance approval for <strong>{rg}</strong> — ${total_savings}/mo savings",
        _section(summary_table, bg="#f8f9fa")
        + _section(context_html)
        + _section(f"<h3 style='margin:0 0 12px;font-size:15px'>Cost Breakdown by Action</h3>"
                   f"{_build_actions_table(actions)}")
        + _approve_reject_buttons(callback, key, stage="finance"),
    )

    return {
        "to": os.getenv("FINANCE_EMAIL", "justinjoy@microsoft.com"),
        "subject": f"💰 Finance Approval — ${total_savings}/mo savings for {rg}",
        "htmlBody": body,
    }


def _build_summary_email(instance_id: str, data: dict) -> dict:
    """Post-execution summary sent to both engineering and finance."""
    rg = data.get("resourceGroup", "unknown")
    total_savings = data.get("estimatedSavingsMonthlyUsd", 0)
    annual_savings = round(total_savings * 12, 2)
    results = data.get("results", [])
    doc_links = data.get("docLinks", [])

    # Results table
    result_rows = ""
    for r in results:
        name, rtype = _resource_short(r.get("resourceId", ""))
        status = r.get("status", "unknown")
        status_badge = (
            "<span style='background:#10b981;color:#fff;padding:2px 8px;"
            "border-radius:4px;font-size:12px'>dry run</span>"
            if status == "dry_run"
            else f"<span style='font-size:12px'>{status}</span>"
        )
        result_rows += (
            f"<tr>"
            f"<td style='padding:8px;border-bottom:1px solid #eee'>"
            f"<strong>{name}</strong><br>"
            f"<span style='font-size:11px;color:#888'>{rtype}</span></td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee;text-align:center'>"
            f"{r.get('actionType', '')}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee;text-align:center'>"
            f"{status_badge}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee;"
            f"font-size:12px;color:#555'>{r.get('details', '')}</td>"
            f"</tr>"
        )
    results_html = (
        "<table style='width:100%;border-collapse:collapse;font-size:13px'>"
        "<thead><tr style='background:#f8f9fa;text-align:left'>"
        "<th style='padding:8px;font-weight:600'>Resource</th>"
        "<th style='padding:8px;font-weight:600;text-align:center'>Action</th>"
        "<th style='padding:8px;font-weight:600;text-align:center'>Status</th>"
        "<th style='padding:8px;font-weight:600'>Details</th>"
        f"</tr></thead><tbody>{result_rows}</tbody></table>"
    )

    # Doc links section
    docs_html = ""
    if doc_links:
        links = "".join(
            f"<li style='margin-bottom:6px'>"
            f"<a href=\"{d.get('url', '#')}\" style='color:#2563eb;text-decoration:none;"
            f"font-weight:600'>{d.get('title', 'Documentation')}</a>"
            f"<br><span style='font-size:12px;color:#888'>{d.get('relevance', '')}</span></li>"
            for d in doc_links[:10]
        )
        docs_html = (
            f"<h3 style='margin:0 0 12px;font-size:15px'>📚 Related Microsoft Learn Docs</h3>"
            f"<ul style='padding-left:20px;margin:0'>{links}</ul>"
        )

    summary_table = (
        "<table style='width:100%;border-collapse:collapse;font-size:14px'>"
        + _summary_row("Resource Group", f"<code>{rg}</code>", bold=True)
        + _summary_row("Monthly Savings", f"${total_savings}", bold=True, color="#10b981")
        + _summary_row("Annual Savings", f"${annual_savings}", bold=True, color="#10b981")
        + _summary_row("Actions Executed", f"{len(results)} (dry run)")
        + _summary_row("Batch ID", f"<code style='font-size:12px'>{instance_id}</code>")
        + "</table>"
    )

    context_html = (
        "<p><strong>✅ Dry Run Complete</strong></p>"
        "<p>The cost optimisation agent has completed a dry-run simulation of all "
        f"approved actions for resource group <code>{rg}</code>. "
        "<strong>No actual changes were made to Azure resources.</strong></p>"
        "<p style='font-size:13px;color:#555'>Review the results below. When ready "
        "to execute for real, re-run the agent with execution mode enabled.</p>"
    )

    body = _wrap_email(
        "Azure Cost Optimisation — Dry Run Complete",
        f"Summary for <strong>{rg}</strong> — ${total_savings}/mo potential savings",
        _section(summary_table, bg="#f8f9fa")
        + _section(context_html)
        + _section(f"<h3 style='margin:0 0 12px;font-size:15px'>Execution Results</h3>"
                   f"{results_html}")
        + (_section(docs_html) if docs_html else ""),
    )

    # Send to both engineering and finance
    eng = os.getenv("ENGINEERING_EMAIL", "justinjoy@microsoft.com")
    fin = os.getenv("FINANCE_EMAIL", "justinjoy@microsoft.com")
    to_list = eng if eng == fin else f"{eng};{fin}"

    return {
        "to": to_list,
        "subject": f"✅ Dry Run Complete — ${total_savings}/mo savings for {rg}",
        "htmlBody": body,
    }


# ---------------------------------------------------------------------------
# Activity registration
# ---------------------------------------------------------------------------

def register_signalr_activities(app, _hub_name: str, _connection_setting: str):
    """Register a notify activity that triggers the Logic App for emails."""

    @app.activity_trigger(input_name="payload")
    def notify_user(payload: dict) -> dict:
        event = payload.get("event", "")
        instance_id = payload.get("instance_id", "")
        data = payload.get("data", {})

        logicapp_url = os.getenv("LOGICAPP_TRIGGER_URL", "").strip()

        # Build email payload based on event type
        email_payload = None
        if event == "approval_required":
            stage = data.get("stage", "finance")
            if stage == "engineering":
                email_payload = _build_engineering_email(instance_id, data)
            else:
                email_payload = _build_finance_email(instance_id, data)
        elif event == "execution_summary":
            email_payload = _build_summary_email(instance_id, data)

        if email_payload and logicapp_url:
            try:
                resp = httpx.post(logicapp_url, json=email_payload, timeout=30)
                logger.info(
                    "Logic App triggered for %s [%s]: HTTP %s",
                    instance_id, event, resp.status_code,
                )
                return {
                    "sent": True,
                    "channel": "logicapp_email",
                    "event": event,
                    "logicapp_status": resp.status_code,
                }
            except Exception as exc:
                logger.warning("Failed to trigger Logic App: %s", exc)
                return {"sent": False, "reason": f"logicapp_trigger_failed: {exc}", "event": event}

        logger.info("notify_user event=%s instance=%s (no email)", event, instance_id)
        return {"sent": False, "reason": "no_email_for_event", "event": event}

    return notify_user
