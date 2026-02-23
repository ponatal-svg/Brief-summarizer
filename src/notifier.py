"""Send email notifications after each pipeline run."""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

# SMTP configuration read from environment variables:
#   SMTP_HOST     - SMTP server hostname (default: smtp.gmail.com)
#   SMTP_PORT     - SMTP port (default: 587)
#   SMTP_USER     - SMTP login username (e.g. your Gmail address)
#   SMTP_PASSWORD - SMTP password or app password
#   SMTP_FROM     - From address (defaults to SMTP_USER)
_DEFAULT_SMTP_HOST = "smtp.gmail.com"
_DEFAULT_SMTP_PORT = 587


def send_run_notification(
    to_addr: str,
    date_str: str,
    digest_entries: list,
    podcast_entries: list,
    skipped_items: list,
    errors: list,
) -> None:
    """Send a run summary email.

    Sends only if SMTP_USER and SMTP_PASSWORD env vars are set.
    Silently skips if not configured.

    Args:
        to_addr:        Recipient email address (from config notify_email).
        date_str:       Run date string, e.g. "2026-02-23".
        digest_entries: Successfully processed YouTube entries.
        podcast_entries: Successfully processed podcast entries.
        skipped_items:  Items detected but not processed (with reason + action).
        errors:         Raw error dicts from the pipeline.
    """
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")

    if not smtp_user or not smtp_password:
        logger.debug("Email notification skipped: SMTP_USER / SMTP_PASSWORD not set")
        return

    smtp_host = os.environ.get("SMTP_HOST", _DEFAULT_SMTP_HOST)
    smtp_port = int(os.environ.get("SMTP_PORT", _DEFAULT_SMTP_PORT))
    from_addr = os.environ.get("SMTP_FROM", smtp_user)

    n_ok = len(digest_entries) + len(podcast_entries)
    n_skipped = len(skipped_items)
    n_errors = len(errors)

    if n_skipped > 0:
        subject = f"⚠️ Morning Brief {date_str} — {n_ok} processed, {n_skipped} skipped"
    else:
        subject = f"✅ Morning Brief {date_str} — {n_ok} items processed"

    plain = _build_plain(date_str, digest_entries, podcast_entries, skipped_items, errors)
    html = _build_html(date_str, digest_entries, podcast_entries, skipped_items, errors)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(smtp_user, smtp_password)
            smtp.sendmail(from_addr, [to_addr], msg.as_string())
        logger.info(f"Email notification sent to {to_addr}")
    except Exception as e:
        # Re-raise so main.py can log it as a warning (non-fatal)
        raise RuntimeError(f"SMTP send failed: {e}") from e


# ---------------------------------------------------------------------------
# Plain-text body
# ---------------------------------------------------------------------------

def _build_plain(
    date_str: str,
    digest_entries: list,
    podcast_entries: list,
    skipped_items: list,
    errors: list,
) -> str:
    lines = [f"Morning Brief Run Report — {date_str}", "=" * 50, ""]

    # Processed
    n_ok = len(digest_entries) + len(podcast_entries)
    lines.append(f"✅ PROCESSED: {n_ok} item(s)")
    lines.append("")

    if digest_entries:
        lines.append("YouTube:")
        for e in digest_entries:
            v = e["video"]
            lines.append(f"  • [{v.channel_name}] {v.title}")
            lines.append(f"    {v.url}")
        lines.append("")

    if podcast_entries:
        lines.append("Podcasts:")
        for e in podcast_entries:
            ep = e["episode"]
            lines.append(f"  • [{ep.show_name}] {ep.title}")
            lines.append(f"    {ep.episode_url}")
        lines.append("")

    # Skipped
    if skipped_items:
        lines.append(f"⚠️  SKIPPED: {len(skipped_items)} item(s) — ACTION REQUIRED")
        lines.append("-" * 50)
        lines.append("")

        youtube_skipped = [s for s in skipped_items if s["type"] == "youtube"]
        podcast_skipped = [s for s in skipped_items if s["type"] == "podcast"]

        if youtube_skipped:
            lines.append("YouTube:")
            for item in youtube_skipped:
                lines.append(f"  • [{item['source']}] {item['title']}")
                lines.append(f"    URL: {item['url']}")
                lines.append(f"    Reason: {item['reason']}")
                lines.append(f"    ➜ Action: {item['action']}")
                lines.append("")

        if podcast_skipped:
            lines.append("Podcasts:")
            for item in podcast_skipped:
                lines.append(f"  • [{item['source']}] {item['title']}")
                lines.append(f"    URL: {item['url']}")
                lines.append(f"    Reason: {item['reason']}")
                lines.append(f"    ➜ Action: {item['action']}")
                lines.append("")
    else:
        lines.append("✅ No skipped items — all content processed successfully.")
        lines.append("")

    # Raw errors (compact)
    if errors:
        lines.append(f"Raw errors ({len(errors)}):")
        for err in errors:
            lines.append(f"  • {err['source']}: {err['message']}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML body
# ---------------------------------------------------------------------------

def _build_html(
    date_str: str,
    digest_entries: list,
    podcast_entries: list,
    skipped_items: list,
    errors: list,
) -> str:
    n_ok = len(digest_entries) + len(podcast_entries)
    n_skipped = len(skipped_items)

    status_color = "#d97706" if n_skipped > 0 else "#16a34a"  # amber / green
    status_icon = "⚠️" if n_skipped > 0 else "✅"

    parts = [
        "<!DOCTYPE html><html><body style='font-family:sans-serif;max-width:640px;margin:auto;color:#1f2937'>",
        f"<h2 style='color:{status_color}'>{status_icon} Morning Brief — {date_str}</h2>",
        f"<p style='color:#6b7280'>{n_ok} processed &nbsp;|&nbsp; {n_skipped} skipped &nbsp;|&nbsp; {len(errors)} errors</p>",
        "<hr style='border:none;border-top:1px solid #e5e7eb'>",
    ]

    # --- Processed ---
    if digest_entries or podcast_entries:
        parts.append("<h3 style='color:#16a34a'>✅ Processed</h3>")

        if digest_entries:
            parts.append("<p><strong>YouTube</strong></p><ul>")
            for e in digest_entries:
                v = e["video"]
                parts.append(
                    f"<li><a href='{v.url}'>{v.title}</a> "
                    f"<span style='color:#6b7280'>— {v.channel_name}</span></li>"
                )
            parts.append("</ul>")

        if podcast_entries:
            parts.append("<p><strong>Podcasts</strong></p><ul>")
            for e in podcast_entries:
                ep = e["episode"]
                parts.append(
                    f"<li><a href='{ep.episode_url}'>{ep.title}</a> "
                    f"<span style='color:#6b7280'>— {ep.show_name}</span></li>"
                )
            parts.append("</ul>")

    # --- Skipped ---
    if skipped_items:
        parts.append("<hr style='border:none;border-top:1px solid #e5e7eb'>")
        parts.append(
            f"<h3 style='color:#d97706'>⚠️ Skipped — Action Required ({n_skipped})</h3>"
        )

        youtube_skipped = [s for s in skipped_items if s["type"] == "youtube"]
        podcast_skipped = [s for s in skipped_items if s["type"] == "podcast"]

        for section_label, items in [("YouTube", youtube_skipped), ("Podcasts", podcast_skipped)]:
            if not items:
                continue
            parts.append(f"<p><strong>{section_label}</strong></p>")
            parts.append(
                "<table style='width:100%;border-collapse:collapse;font-size:14px'>"
                "<tr style='background:#f3f4f6'>"
                "<th style='text-align:left;padding:6px 8px'>Item</th>"
                "<th style='text-align:left;padding:6px 8px'>Reason</th>"
                "<th style='text-align:left;padding:6px 8px'>Action</th>"
                "</tr>"
            )
            for i, item in enumerate(items):
                bg = "#ffffff" if i % 2 == 0 else "#f9fafb"
                parts.append(
                    f"<tr style='background:{bg}'>"
                    f"<td style='padding:6px 8px;vertical-align:top'>"
                    f"<strong>{item['source']}</strong><br>"
                    f"<a href='{item['url']}' style='color:#2563eb;font-size:13px'>{item['title']}</a>"
                    f"</td>"
                    f"<td style='padding:6px 8px;vertical-align:top;color:#6b7280'>{item['reason']}</td>"
                    f"<td style='padding:6px 8px;vertical-align:top;color:#b45309;font-weight:500'>{item['action']}</td>"
                    f"</tr>"
                )
            parts.append("</table>")
    else:
        parts.append(
            "<p style='color:#16a34a'>✅ No skipped items — all content processed successfully.</p>"
        )

    # --- Raw errors (collapsed) ---
    if errors:
        parts.append("<hr style='border:none;border-top:1px solid #e5e7eb'>")
        parts.append(f"<details><summary style='color:#6b7280;cursor:pointer'>Raw errors ({len(errors)})</summary>")
        parts.append("<ul style='font-size:13px;color:#6b7280'>")
        for err in errors:
            parts.append(f"<li><strong>{err['source']}</strong>: {err['message']}</li>")
        parts.append("</ul></details>")

    parts.append(
        "<hr style='border:none;border-top:1px solid #e5e7eb'>"
        "<p style='color:#9ca3af;font-size:12px'>Morning Brief Summarizer</p>"
        "</body></html>"
    )

    return "\n".join(parts)
