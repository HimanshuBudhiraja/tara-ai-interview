"""Outbound email for participant invitations, over plain SMTP.

Configured entirely by environment, so any provider with an SMTP endpoint works
(SES, SendGrid, Postmark, Microsoft 365, Gmail Workspace):

    SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM,
    SMTP_STARTTLS (true)

When SMTP_HOST or SMTP_FROM is missing, `configured()` is False and nothing
pretends to send: the builder shows the invitation to copy instead.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def configured() -> bool:
    return bool(_env("SMTP_HOST") and _env("SMTP_FROM"))


def sender() -> str:
    return _env("SMTP_FROM")


class EmailNotSent(Exception):
    """The message could not be handed to the mail server."""


def send(to: str, subject: str, text: str, html: str | None = None) -> None:
    if not configured():
        raise EmailNotSent("Email isn't set up on this server.")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender(), to, subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    port = int(_env("SMTP_PORT", "587") or 587)
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(_env("SMTP_HOST"), port, timeout=20, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(_env("SMTP_HOST"), port, timeout=20)
            if _env("SMTP_STARTTLS", "true").lower() != "false":
                server.starttls(context=ssl.create_default_context())
        with server:
            if _env("SMTP_USER"):
                server.login(_env("SMTP_USER"), _env("SMTP_PASSWORD"))
            server.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailNotSent(f"The mail server refused the message ({type(exc).__name__}).") from exc
