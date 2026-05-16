"""
Daily tender digest — sends strong matches to Teams and email via Microsoft Graph API.

Triggered by the scheduler at 08:00 UTC (09:00 BST) — 30 minutes before
the PANZ/City Leap standup and 1 hour before the Frameworks meeting.

Delivery:
  1. Teams channel message via incoming webhook (if TEAMS_WEBHOOK_URL configured)
  2. Email via Microsoft Graph API to configured recipients
  
Environment variables required:
  TEAMS_WEBHOOK_URL   — Incoming webhook URL from Teams channel settings
  DIGEST_EMAIL_TO     — Comma-separated recipient emails
  GRAPH_CLIENT_ID     — Azure AD app client ID (for email via Graph)
  GRAPH_CLIENT_SECRET — Azure AD app client secret
  GRAPH_TENANT_ID     — Azure AD tenant ID
  DIGEST_FROM_EMAIL   — Sender email address
"""

import logging
import httpx
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import APIRouter

from app.dependencies import cache, CACHE_KEY_TENDERS
from app.config import settings
from app.models.tender import Tender

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/digest", tags=["Digest"])


# ── Score thresholds ──────────────────────────────────────────────────────────
STRONG_THRESHOLD  = 7   # Score ≥ 7 → Strong match (always included)
LIKELY_THRESHOLD  = 6   # Score ≥ 6 → Likely relevant (included to fill digest)
DEADLINE_DAYS     = 14  # Tenders with deadline within 14 days flagged urgently
MAX_TENDERS       = 10  # Maximum tenders in digest


def _days_until(deadline: Optional[datetime]) -> Optional[int]:
    if not deadline:
        return None
    now = datetime.now(timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return (deadline - now).days


def _urgency_label(days: Optional[int]) -> str:
    if days is None:
        return ""
    if days < 0:
        return "🔴 CLOSED"
    if days <= 7:
        return f"🔴 {days}d left"
    if days <= 14:
        return f"🟡 {days}d left"
    return f"🟢 {days}d"


# Categories excluded from digest — closed/awarded tenders not actionable
EXCLUDED_CATEGORIES = {"Awarded Contract", "Award", "Contract"}

# Only these categories are actionable for bidding
ACTIONABLE_CATEGORIES = {"Opportunity", "Future Opportunity", "Early Engagement",
                          "Tender", "Pipeline", "Planning"}


def _select_tenders(tenders: List[Tender]) -> List[Tender]:
    """
    Select the most relevant actionable tenders for the digest.
    Excludes awarded/closed tenders — only open opportunities shown.
    """
    # Filter to actionable only — no awarded contracts, no closed tenders
    actionable = [
        t for t in tenders
        if t.category not in EXCLUDED_CATEGORIES
        and t.category in ACTIONABLE_CATEGORIES
        # Exclude tenders with past deadlines
        and (_days_until(t.deadline) is None or _days_until(t.deadline) >= 0)
    ]

    # Watchlist matches — always include regardless of score
    watchlist = [
        t for t in actionable
        if getattr(t, 'watchlist_match', False)
        and t not in []
    ]
    watchlist.sort(key=lambda t: t.score, reverse=True)

    # Strong matches (score ≥ 7)
    strong = [t for t in actionable if t.score >= STRONG_THRESHOLD and t not in watchlist]
    strong.sort(key=lambda t: t.score, reverse=True)

    # Urgent tenders (deadline within 14 days) — include even if score < 7
    urgent = [
        t for t in actionable
        if t.score >= LIKELY_THRESHOLD
        and t.deadline
        and 0 <= _days_until(t.deadline) <= DEADLINE_DAYS
        and t not in strong
    ]
    urgent.sort(key=lambda t: t.deadline)

    # Fill remaining slots with likely relevant (score ≥ 4)
    likely = [
        t for t in actionable
        if t.score >= LIKELY_THRESHOLD
        and t not in strong
        and t not in urgent
    ]
    likely.sort(key=lambda t: t.score, reverse=True)

    selected = (watchlist + strong + urgent + likely)[:MAX_TENDERS]
    return selected


def _format_teams_message(tenders: List[Tender], run_date: datetime) -> dict:
    """
    Format tenders as a Teams Workflows-compatible adaptive card payload.
    Uses the format required by the 'Post to channel when webhook received' workflow.
    """
    date_str     = run_date.strftime("%A %d %B %Y")
    strong_count = sum(1 for t in tenders if t.score >= STRONG_THRESHOLD)
    urgent_count = sum(
        1 for t in tenders
        if t.deadline and 0 <= _days_until(t.deadline) <= DEADLINE_DAYS
    )

    body_items = [
        {
            "type": "TextBlock",
            "text": "⚡ Nordic Energy — Tender Intelligence",
            "weight": "Bolder",
            "size": "Large",
            "color": "Accent"
        },
        {
            "type": "TextBlock",
            "text": f"**{date_str}** · {len(tenders)} tenders · {strong_count} strong · {urgent_count} urgent",
            "wrap": True,
            "isSubtle": True,
            "spacing": "None"
        }
    ]

    for t in tenders:
        days      = _days_until(t.deadline)
        urgency   = _urgency_label(days)
        score_emoji = "🟢" if t.score >= 7 else "🟡" if t.score >= 4 else "🔴"
        services  = ", ".join(
            s.replace("Service 0", "S").split(":")[0]
            for s in (t.all_matched_scopes or t.matched_scopes or [])
        ) or "General"
        value     = t.value if t.value != "Value not stated" else "TBC"
        deadline  = urgency or (t.deadline.strftime("%d %b %Y") if t.deadline else "Not stated")

        body_items.append({
            "type": "Container",
            "style": "emphasis" if t.score >= 7 else "default",
            "separator": True,
            "items": [
                {
                    "type": "TextBlock",
                    "text": f"{score_emoji} **{t.title[:80]}**",
                    "wrap": True,
                    "weight": "Bolder",
                    "size": "Small"
                },
                {
                    "type": "ColumnSet",
                    "columns": [
                        {
                            "type": "Column",
                            "width": "stretch",
                            "items": [{"type": "FactSet", "facts": [
                                {"title": "Authority", "value": t.authority[:50]},
                                {"title": "Value",     "value": value},
                            ]}]
                        },
                        {
                            "type": "Column",
                            "width": "stretch",
                            "items": [{"type": "FactSet", "facts": [
                                {"title": "Score",    "value": f"{t.score}/10"},
                                {"title": "Deadline", "value": deadline},
                            ]}]
                        },
                        {
                            "type": "Column",
                            "width": "stretch",
                            "items": [{"type": "FactSet", "facts": [
                                {"title": "Services", "value": services},
                                {"title": "Source",   "value": t.source},
                            ]}]
                        }
                    ]
                },
                {
                    "type": "ActionSet",
                    "actions": [{
                        "type":  "Action.OpenUrl",
                        "title": "View Notice →",
                        "url":   t.url
                    }]
                }
            ]
        })

    body_items.append({
        "type": "TextBlock",
        "text": "Scores computed against NE's four service areas · [Open Dashboard](http://localhost:5173)",
        "wrap": True,
        "isSubtle": True,
        "size": "Small",
        "separator": True
    })

    # Workflows webhook uses this exact envelope
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "contentUrl": None,
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type":    "AdaptiveCard",
                "version": "1.5",
                "body":    body_items
            }
        }]
    }


def _format_email_html(tenders: List[Tender], run_date: datetime) -> str:
    """Format tenders as HTML email."""
    date_str = run_date.strftime("%A %d %B %Y")
    strong_count = sum(1 for t in tenders if t.score >= STRONG_THRESHOLD)
    urgent_count = sum(
        1 for t in tenders
        if t.deadline and 0 <= _days_until(t.deadline) <= DEADLINE_DAYS
    )

    rows = ""
    for t in tenders:
        days = _days_until(t.deadline)
        urgency = _urgency_label(days)
        deadline_str = (
            urgency if urgency else
            (t.deadline.strftime("%d %b %Y") if t.deadline else "Not stated")
        )
        score_color = "#00c853" if t.score >= 7 else "#f9a825" if t.score >= 4 else "#e53935"
        services = "<br>".join(
            f"• {s}" for s in (t.all_matched_scopes or t.matched_scopes or ["General"])
        )
        rows += f"""
        <tr style="border-bottom:1px solid #eee">
            <td style="padding:12px;vertical-align:top;min-width:300px">
                <a href="{t.url}" style="color:#1a237e;font-weight:bold;text-decoration:none">{t.title[:90]}</a><br>
                <span style="color:#666;font-size:12px">{t.authority}</span>
            </td>
            <td style="padding:12px;vertical-align:top;text-align:center">
                <span style="background:{score_color};color:white;padding:3px 8px;border-radius:12px;font-weight:bold">{t.score}/10</span>
            </td>
            <td style="padding:12px;vertical-align:top;font-size:13px">{t.value}</td>
            <td style="padding:12px;vertical-align:top;font-size:13px">{deadline_str}</td>
            <td style="padding:12px;vertical-align:top;font-size:12px;color:#555">{services}</td>
            <td style="padding:12px;vertical-align:top;font-size:12px">{t.source}</td>
        </tr>"""

    return f"""
    <html><body style="font-family:Segoe UI,Arial,sans-serif;color:#222;max-width:900px;margin:0 auto">
    <div style="background:#0a0f1a;color:white;padding:20px 24px;border-radius:8px 8px 0 0">
        <h2 style="margin:0;color:#00e5a0">⚡ Nordic Energy — Tender Intelligence</h2>
        <p style="margin:4px 0 0;color:#aaa">{date_str} · {len(tenders)} tenders · {strong_count} strong matches · {urgent_count} urgent deadlines</p>
    </div>
    <table style="width:100%;border-collapse:collapse;border:1px solid #ddd;border-top:none">
        <thead>
            <tr style="background:#f5f5f5">
                <th style="padding:10px 12px;text-align:left;font-size:12px">TENDER</th>
                <th style="padding:10px 12px;text-align:center;font-size:12px">SCORE</th>
                <th style="padding:10px 12px;text-align:left;font-size:12px">VALUE</th>
                <th style="padding:10px 12px;text-align:left;font-size:12px">DEADLINE</th>
                <th style="padding:10px 12px;text-align:left;font-size:12px">SERVICES</th>
                <th style="padding:10px 12px;text-align:left;font-size:12px">SOURCE</th>
            </tr>
        </thead>
        <tbody>{rows}</tbody>
    </table>
    <div style="padding:12px;background:#f9f9f9;border:1px solid #ddd;border-top:none;font-size:11px;color:#888">
        Scores computed against Nordic Energy's four service areas: Renewable Energy Opportunity Identification · 
        Energy Feasibility Studies · Energy System Optimisation · Business Case Development.
        Data from Find a Tender (FaT) and Contracts Finder (CF).
    </div>
    </body></html>"""


async def send_teams_digest(tenders: List[Tender]) -> bool:
    """
    Send digest to Teams channel via Workflows webhook.
    Supports both Adaptive Card format and simple text fallback.
    The 'Send webhook alerts to a channel' Workflows template accepts
    { "text": "..." } as a simple payload.
    """
    webhook_url = getattr(settings, 'teams_webhook_url', None)
    if not webhook_url:
        logger.info("TEAMS_WEBHOOK_URL not configured — skipping Teams digest")
        return False

    run_date     = datetime.now(timezone.utc)
    strong_count = sum(1 for t in tenders if t.score >= STRONG_THRESHOLD)
    urgent_count = sum(
        1 for t in tenders
        if t.deadline and 0 <= _days_until(t.deadline) <= DEADLINE_DAYS
    )
    date_str = run_date.strftime("%A %d %B %Y")

    # Try Adaptive Card first, fall back to simple text
    payloads_to_try = [
        _format_teams_message(tenders, run_date),   # Adaptive Card
        _format_teams_simple(tenders, run_date),     # Simple text fallback
    ]

    async with httpx.AsyncClient(timeout=15.0) as client:
        for payload in payloads_to_try:
            try:
                resp = await client.post(webhook_url, json=payload)
                if resp.status_code < 300:
                    logger.info("Teams digest sent — %d tenders (%d strong)",
                                len(tenders), strong_count)
                    return True
                logger.warning("Teams webhook returned %d — trying fallback", resp.status_code)
            except Exception as e:
                logger.warning("Teams digest attempt failed: %s", e)

    logger.error("All Teams digest attempts failed")
    return False


def _format_teams_simple(tenders: List[Tender], run_date: datetime) -> dict:
    """Simple text fallback for Teams Workflows webhook."""
    date_str     = run_date.strftime("%d %b %Y")
    strong_count = sum(1 for t in tenders if t.score >= STRONG_THRESHOLD)

    lines = [f"⚡ **Nordic Energy Tender Intelligence — {date_str}**"]
    lines.append(f"{len(tenders)} tenders - {strong_count} strong matches\n")

    for t in tenders:
        days     = _days_until(t.deadline)
        urgency  = _urgency_label(days)
        emoji    = "🟢" if t.score >= 7 else "🟡"
        deadline = urgency or (t.deadline.strftime("%d %b %Y") if t.deadline else "TBC")
        lines.append(
            f"{emoji} **{t.title[:70]}**\n"
            f"  {t.authority} | Score: {t.score}/10 | Value: {t.value} | Deadline: {deadline}\n"
            f"  {t.url}\n"
        )

    return {"text": "\n".join(lines)}


async def _send_email_smtp(
    tenders: List[Tender],
    recipients_str: str,
    from_email: str,
    app_password: str,
) -> bool:
    """
    Send email via SMTP using Microsoft 365 app password.
    No admin consent required — works with any M365 mailbox.

    To get an app password:
    1. Go to https://mysignins.microsoft.com/security-info
    2. Add sign-in method → App password
    3. Name it "Nordic Energy Tender Digest" → copy the password
    4. Add to .env: SMTP_APP_PASSWORD=xxxx xxxx xxxx xxxx
    """
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    recipients = [e.strip() for e in recipients_str.split(",") if e.strip()]
    run_date   = datetime.now(timezone.utc)
    strong_count = sum(1 for t in tenders if t.score >= STRONG_THRESHOLD)

    subject = (
        f"⚡ Tender Intelligence — {strong_count} strong match"
        f"{'es' if strong_count != 1 else ''} · "
        f"{run_date.strftime('%d %b %Y')}"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_email
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(_format_email_html(tenders, run_date), "html"))

    try:
        with smtplib.SMTP("smtp.office365.com", 587) as server:
            server.starttls()
            server.login(from_email, app_password)
            server.sendmail(from_email, recipients, msg.as_string())
        logger.info("SMTP digest sent to %s — %d tenders", recipients, len(tenders))
        return True
    except Exception as e:
        logger.error("SMTP digest failed: %s", e)
        return False


async def send_email_digest(tenders: List[Tender]) -> bool:
    """
    Send digest email. Tries two methods in order:
    1. Microsoft Graph API (requires Azure AD app with Mail.Send permission)
    2. SMTP with app password (simpler — no admin consent needed)
    """
    recipients_str = getattr(settings, 'digest_email_to', None)
    from_email     = getattr(settings, 'digest_from_email', None)

    if not recipients_str or not from_email:
        logger.info("Email not configured — skipping email digest")
        return False

    # Try SMTP first (simpler, no admin consent needed)
    smtp_password = getattr(settings, 'smtp_app_password', None)
    if smtp_password:
        return await _send_email_smtp(tenders, recipients_str, from_email, smtp_password)

    # Fall back to Graph API
    client_id      = getattr(settings, 'graph_client_id', None)
    client_secret  = getattr(settings, 'graph_client_secret', None)
    tenant_id      = getattr(settings, 'graph_tenant_id', None)

    if not all([client_id, client_secret, tenant_id]):
        logger.info("Neither SMTP nor Graph API configured — skipping email digest")
        return False

    recipients = [e.strip() for e in recipients_str.split(",") if e.strip()]
    run_date   = datetime.now(timezone.utc)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get access token
            token_resp = await client.post(
                f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type":    "client_credentials",
                    "client_id":     client_id,
                    "client_secret": client_secret,
                    "scope":         "https://graph.microsoft.com/.default",
                },
            )
            token_resp.raise_for_status()
            token = token_resp.json()["access_token"]

            # Send email
            strong_count = sum(1 for t in tenders if t.score >= STRONG_THRESHOLD)
            subject = (
                f"⚡ Tender Intelligence — {strong_count} strong match{'es' if strong_count != 1 else ''} "
                f"· {run_date.strftime('%d %b %Y')}"
            )
            email_payload = {
                "message": {
                    "subject": subject,
                    "body": {
                        "contentType": "HTML",
                        "content": _format_email_html(tenders, run_date),
                    },
                    "toRecipients": [
                        {"emailAddress": {"address": r}} for r in recipients
                    ],
                    "from": {
                        "emailAddress": {"address": from_email}
                    }
                },
                "saveToSentItems": True,
            }
            send_resp = await client.post(
                f"https://graph.microsoft.com/v1.0/users/{from_email}/sendMail",
                json=email_payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            send_resp.raise_for_status()
            logger.info("Email digest sent to %s — %d tenders", recipients, len(tenders))
            return True

    except Exception as e:
        logger.error("Email digest failed: %s", e)
        return False


@router.post("/send", summary="Send daily tender digest to Teams and email")
async def trigger_digest():
    """
    Manually trigger the daily tender digest.
    Sends to Teams (if TEAMS_WEBHOOK_URL configured) and email (if Graph API configured).
    """
    tenders = cache.get(CACHE_KEY_TENDERS)
    if not tenders:
        return {"status": "error", "message": "Cache not populated — run /refresh first"}

    selected = _select_tenders(tenders)

    teams_ok = await send_teams_digest(selected)
    email_ok = await send_email_digest(selected)

    return {
        "status":         "ok",
        "tenders_sent":   len(selected),
        "strong_matches": sum(1 for t in selected if t.score >= STRONG_THRESHOLD),
        "teams":          "sent" if teams_ok else "not configured",
        "email":          "sent" if email_ok else "not configured",
        "tenders":        [{"title": t.title, "score": t.score, "authority": t.authority} for t in selected],
    }


async def run_scheduled_digest():
    """Called by scheduler — wraps trigger_digest for cron use."""
    tenders = cache.get(CACHE_KEY_TENDERS)
    if not tenders:
        logger.warning("Digest skipped — cache empty")
        return
    selected = _select_tenders(tenders)
    await send_teams_digest(selected)
    await send_email_digest(selected)
    logger.info(
        "Scheduled digest complete — %d tenders (%d strong)",
        len(selected),
        sum(1 for t in selected if t.score >= STRONG_THRESHOLD),
    )