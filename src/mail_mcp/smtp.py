"""Composing and sending mail.

Sending is the only thing this server does that the outside world can see, so
it is also the only thing worth attacking. Every path through here goes past
the recipient allowlist first.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

import anyio

from .guard import extract_address
from .settings import Settings
from .tls import client_context

logger = logging.getLogger(__name__)


class SendError(RuntimeError):
    pass


def build_message(
    settings: Settings,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = (
        formataddr((settings.from_name, settings.smtp_user))
        if settings.from_name
        else settings.smtp_user
    )
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        # Keeping References intact is what makes clients thread the reply.
        msg["References"] = f"{references} {in_reply_to}".strip() if references else in_reply_to
    msg.set_content(body)
    return msg


def strip_self(settings: Settings, addresses: list[str]) -> list[str]:
    """Drop our own address, so a reply-all cannot loop mail back at us."""
    own = extract_address(settings.smtp_user)
    return [a for a in addresses if extract_address(a) != own]


def _send_sync(settings: Settings, msg: EmailMessage, recipients: list[str]) -> None:
    context = client_context()
    if settings.smtp_port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=30, context=context
        )
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        server.starttls(context=context)
    try:
        server.login(settings.smtp_user, settings.smtp_pass)
        server.send_message(msg, to_addrs=recipients)
    finally:
        server.quit()


async def send(settings: Settings, msg: EmailMessage, recipients: list[str]) -> None:
    try:
        await anyio.to_thread.run_sync(lambda: _send_sync(settings, msg, recipients))
    except Exception as exc:
        raise SendError(f"SMTP error: {exc}") from exc
