"""
helpers/email_utils.py — Email sending for Deed & Plat Helper SaaS.

Uses SMTP (Gmail, Mailgun SMTP, or any provider). Falls back to
console-logging the email content if SMTP is not configured — useful
for local dev and self-hosted deployments.

Required env vars (all optional — enables email sending when set):
  DEED_SMTP_HOST     e.g. smtp.gmail.com
  DEED_SMTP_PORT     e.g. 587
  DEED_SMTP_USER     e.g. yourapp@gmail.com
  DEED_SMTP_PASS     your app password (Gmail: create an App Password)
  DEED_FROM_EMAIL    defaults to DEED_SMTP_USER
  DEED_APP_URL       used to build reset links
"""

from __future__ import annotations

import logging
import os
import smtplib
import textwrap
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

_SMTP_HOST   = os.environ.get("DEED_SMTP_HOST", "")
_SMTP_PORT   = int(os.environ.get("DEED_SMTP_PORT", "587"))
_SMTP_USER   = os.environ.get("DEED_SMTP_USER", "")
_SMTP_PASS   = os.environ.get("DEED_SMTP_PASS", "")
_FROM_EMAIL  = os.environ.get("DEED_FROM_EMAIL", _SMTP_USER) or "noreply@deedplat.local"
_APP_URL     = os.environ.get("DEED_APP_URL", "http://localhost:5000")
_SMTP_READY  = bool(_SMTP_HOST and _SMTP_USER and _SMTP_PASS)


def _send_email(to: str, subject: str, body_text: str, body_html: str = "") -> bool:
    """Send an email. Returns True on success. Falls back to console on failure."""
    if not _SMTP_READY:
        # Dev/offline fallback — print to server console
        print("\n" + "="*60)
        print(f"[EMAIL → {to}]")
        print(f"Subject: {subject}")
        print("-"*60)
        print(body_text)
        print("="*60 + "\n", flush=True)
        return True   # treat as success for UX flow continuity

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"Deed & Plat Helper <{_FROM_EMAIL}>"
        msg["To"]      = to
        msg.attach(MIMEText(body_text, "plain"))
        if body_html:
            msg.attach(MIMEText(body_html, "html"))

        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(_SMTP_USER, _SMTP_PASS)
            smtp.sendmail(_FROM_EMAIL, [to], msg.as_string())

        log.info(f"[email] Sent '{subject}' to {to}")
        return True
    except Exception as e:
        log.error(f"[email] Failed to send to {to}: {e}")
        return False


# ── Public send functions ─────────────────────────────────────────────────────

def send_password_reset(to_email: str, reset_link: str) -> bool:
    """Send a password reset email with the one-time reset link."""
    subject = "Reset your Deed & Plat Helper password"
    body_text = textwrap.dedent(f"""
        Hi,

        We received a request to reset the password for your Deed & Plat Helper account.

        Click the link below to choose a new password (valid for 1 hour):

          {reset_link}

        If you didn't request a password reset, you can ignore this email — your
        account is safe and your password has not changed.

        — Deed & Plat Helper
    """).strip()

    body_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px">
      <h2 style="color:#1a1a2e">Reset your password</h2>
      <p>We received a request to reset the password for your Deed &amp; Plat Helper account.</p>
      <p style="margin:24px 0">
        <a href="{reset_link}" style="background:#4facfe;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;display:inline-block">
          Reset Password
        </a>
      </p>
      <p style="color:#666;font-size:12px">Link expires in 1 hour. If you didn't request this, ignore this email.</p>
      <hr style="border:none;border-top:1px solid #eee;margin:24px 0">
      <p style="color:#999;font-size:11px">Deed &amp; Plat Helper · {_APP_URL}</p>
    </div>
    """
    return _send_email(to_email, subject, body_text, body_html)


def send_welcome(to_email: str) -> bool:
    """Send a welcome email to a newly registered user."""
    subject = "Welcome to Deed & Plat Helper"
    body_text = textwrap.dedent(f"""
        Welcome!

        Your Deed & Plat Helper account is ready. You're on the Free plan —
        10 searches per month to start.

        To get started:
          1. Open {_APP_URL}
          2. Sign in with your email and password
          3. Configure your county connection in Settings

        Upgrade to Pro for unlimited searches and advanced features like OCR,
        ArcGIS adjoiners, and DXF export.

        — Deed & Plat Helper
    """).strip()

    body_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px">
      <h2 style="color:#1a1a2e">Welcome to Deed &amp; Plat Helper! 🦅</h2>
      <p>Your account is ready. You're on the <strong>Free plan</strong> — 10 searches/month to get started.</p>
      <p style="margin:24px 0">
        <a href="{_APP_URL}" style="background:#4facfe;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;display:inline-block">
          Open the App
        </a>
      </p>
      <p>Configure your county connection in <strong>Settings</strong>, then start researching!</p>
      <hr style="border:none;border-top:1px solid #eee;margin:24px 0">
      <p style="color:#999;font-size:11px">Deed &amp; Plat Helper · {_APP_URL}</p>
    </div>
    """
    return _send_email(to_email, subject, body_text, body_html)


def send_admin_new_user_notification(new_email: str, admin_email: str) -> bool:
    """Notify the admin when a new user registers."""
    if not admin_email:
        return False
    subject = f"New registration: {new_email}"
    body_text = f"New user registered: {new_email}\n\nView in admin panel: {_APP_URL}/admin"
    return _send_email(admin_email, subject, body_text)


def send_subscription_cancelled(to_email: str) -> bool:
    """Notify a user their subscription was cancelled and they're back on Free."""
    subject = "Your Deed & Plat Helper subscription has ended"
    body_text = textwrap.dedent(f"""
        Hi,

        Your Deed & Plat Helper Pro subscription has been cancelled and your
        account is now on the Free plan (10 searches/month).

        To continue with unlimited access, resubscribe anytime at:
          {_APP_URL}

        Your research data is safe and has not been deleted.

        — Deed & Plat Helper
    """).strip()
    return _send_email(to_email, subject, body_text)
