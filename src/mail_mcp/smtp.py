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

logger = logging.getLogger(__name__)


class SendError(RuntimeError):
    """A failed delivery, safe to show and to log: its text is ours, never the server's.

    smtplib puts the server's wording, and for refused recipients their addresses, into its
    exception text. `smtp_failure` keeps only a stable `code` and the name of the original class.
    """

    def __init__(self, message: str, *, code: str = "smtp_error", source: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.source = source


def smtp_failure(exc: BaseException) -> SendError:
    """The public form of an SMTP failure: a stable code and generic text, never the raw text."""
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        code, text = "smtp_recipients_refused", "The mail server refused one or more recipients."
    elif isinstance(exc, smtplib.SMTPAuthenticationError):
        code, text = "smtp_login_failed", "The mail server refused the login."
    elif isinstance(exc, smtplib.SMTPSenderRefused):
        code, text = "smtp_sender_refused", "The mail server refused the sender address."
    elif isinstance(exc, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError)):
        code, text = "smtp_unreachable", "Could not reach the mail server, or the connection was lost."
    elif isinstance(exc, smtplib.SMTPException):
        # Before OSError: SMTPException subclasses it, so a server's answer is a rejection.
        code, text = "smtp_rejected", "The mail server rejected the message."
    elif isinstance(exc, OSError):
        code, text = "smtp_unreachable", "Could not reach the mail server, or the connection was lost."
    else:
        code, text = "smtp_error", "Sending the mail failed."
    return SendError(f"{text} ({code})", code=code, source=type(exc).__name__)


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
    if settings.smtp_port == 465:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=30
        )
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        server.starttls()
    try:
        server.login(settings.smtp_user, settings.smtp_pass)
        server.send_message(msg, to_addrs=recipients)
    finally:
        server.quit()


async def send(settings: Settings, msg: EmailMessage, recipients: list[str]) -> None:
    failure: SendError | None = None
    try:
        await anyio.to_thread.run_sync(lambda: _send_sync(settings, msg, recipients))
    except Exception as exc:
        failure = smtp_failure(exc)
        logger.warning("SMTP delivery failed (%s)", failure.source)
    if failure is not None:
        # Raised outside the except block on purpose: the raw exception is neither the cause nor
        # the context, so no traceback printer further up can show what the server said.
        raise failure
