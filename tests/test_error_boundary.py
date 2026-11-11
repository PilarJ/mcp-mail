"""IMAP and SMTP failures reach callers and logs as a stable code and generic text only.

imap-tools and smtplib put the server's raw answer (and, for refused recipients, the
addresses) into their exception text. Whatever a server writes is not ours to pass on: it
can carry personal data into a tool result that a language model reads and into retained
logs. These tests make the servers fail with a marker that stands for such text and check
every place it could surface: the tool result or tool error, the exception chain, the log
records, and what is written to stderr by the framework's own exception logging.
"""

from __future__ import annotations

import json
import logging
import smtplib

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from imap_wire import WireServer

from mail_mcp import imap as imap_module
from mail_mcp import smtp, tools
from mail_mcp.imap import ImapClient, MailError
from mail_mcp.settings import Settings
from mail_mcp.smtp import SendError

pytestmark = pytest.mark.anyio

MARKER = "PRIVATE-MARKER"
ADDRESS = "synthetic@example.test"
SERVER_TEXT = f"[ALERT] {MARKER} quota for {ADDRESS}"
# Everything that must never appear in a result, an error or a log.
FORBIDDEN = (MARKER, ADDRESS, "ALERT", "quota", "bob@secret.example")
SEND = {"to": ["someone@example.test"], "subject": "Test subject", "body": "Hello", "confirm": True}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_settings(tmp_path) -> Settings:
    return Settings(
        MAILBOX_LABEL="box@example.test", BASE_URL="https://mcp.example.test",
        GITHUB_CLIENT_ID="x", GITHUB_CLIENT_SECRET="x",
        IMAP_HOST="imap.example.test", IMAP_USER="box@example.test", IMAP_PASS="x",
        SMTP_HOST="smtp.example.test", SMTP_USER="box@example.test", SMTP_PASS="x",
        ALLOWED_RECIPIENTS="@example.test", DATA_DIR=str(tmp_path),
    )


async def call(tmp_path, tool: str, arguments: dict) -> dict:
    mcp = FastMCP("test")
    tools.register(mcp, make_settings(tmp_path))
    async with Client(mcp) as client:
        result = await client.call_tool(tool, arguments)
    return result.structured_content or result.data


@pytest.fixture
def logs(caplog, capfd):
    """Everything logged during the test: records at any level plus what reached stderr
    (the framework's own exception logger writes there)."""
    caplog.set_level(logging.DEBUG)

    def collected() -> str:
        captured = capfd.readouterr()
        return caplog.text + captured.out + captured.err

    return collected


def assert_clean(text: str) -> None:
    for forbidden in FORBIDDEN:
        assert forbidden not in text, f"{forbidden!r} leaked"


# ---------- IMAP ----------

IMAP_FAILURES = {
    "login-refused": ({"login_reply": f"NO {SERVER_TEXT}"}, "imap_login_failed", "MailboxLoginError"),
    "select-refused": ({"select_reply": f"NO {SERVER_TEXT}"}, "imap_command_failed", "MailboxFolderSelectError"),
}
imap_failure = pytest.mark.parametrize(
    "knobs,code,error_class", IMAP_FAILURES.values(), ids=IMAP_FAILURES.keys()
)


@imap_failure
async def test_mail_status_reports_a_stable_code_not_the_server_text(
    tmp_path, monkeypatch, logs, knobs, code, error_class
):
    monkeypatch.setattr(imap_module, "MailBox", WireServer(**knobs).mailbox_class())

    result = await call(tmp_path, "mail_status", {})

    assert result["connected"] is False
    assert_clean(json.dumps(result))
    assert result["error"] == code
    assert result["detail"] and "IMAP error" not in result["detail"]
    assert_clean(logs())


@imap_failure
async def test_a_failing_read_tool_returns_a_clean_tool_error(
    tmp_path, monkeypatch, logs, knobs, code, error_class
):
    monkeypatch.setattr(imap_module, "MailBox", WireServer(**knobs).mailbox_class())

    with pytest.raises(ToolError) as caught:
        await call(tmp_path, "mail_list", {})

    assert_clean(str(caught.value))
    assert code in str(caught.value)
    assert_clean(logs())


@imap_failure
async def test_the_imap_error_carries_no_raw_exception_and_no_chain(
    tmp_path, monkeypatch, knobs, code, error_class
):
    monkeypatch.setattr(imap_module, "MailBox", WireServer(**knobs).mailbox_class())

    with pytest.raises(MailError) as caught:
        await ImapClient(make_settings(tmp_path)).list_messages("INBOX", 5, False, None, None, None)

    error = caught.value
    assert_clean(str(error))
    assert error.code == code
    # Nothing to log by accident: the raw exception is neither the cause nor the context.
    assert error.__cause__ is None and error.__context__ is None


@imap_failure
async def test_an_imap_failure_is_logged_as_its_class_only(
    tmp_path, monkeypatch, caplog, knobs, code, error_class
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(imap_module, "MailBox", WireServer(**knobs).mailbox_class())

    with pytest.raises(ToolError):
        await call(tmp_path, "mail_list", {})

    ours = [r for r in caplog.records if r.name == "mail_mcp.imap" and r.levelno == logging.WARNING]
    assert len(ours) == 1
    assert error_class in ours[0].getMessage()
    assert ours[0].exc_info is None


async def test_an_unreachable_server_is_reported_without_its_text(tmp_path, monkeypatch, logs):
    def refuse(host, port=993):
        raise ConnectionRefusedError(f"[Errno 61] {SERVER_TEXT}")

    monkeypatch.setattr(imap_module, "MailBox", refuse)

    status = await call(tmp_path, "mail_status", {})
    with pytest.raises(ToolError) as caught:
        await call(tmp_path, "mail_list", {})

    assert status["error"] == "imap_unreachable"
    assert_clean(json.dumps(status) + str(caught.value))
    assert "imap_unreachable" in str(caught.value)
    assert_clean(logs())


async def test_a_bad_value_the_library_rejects_is_not_echoed_back(tmp_path, monkeypatch, logs):
    """imap-tools names the offending uid in its message; the caller's text is not ours to log."""
    monkeypatch.setattr(imap_module, "MailBox", WireServer().mailbox_class())

    with pytest.raises(ToolError) as caught:
        await call(tmp_path, "mail_mark", {"uids": [f"1 {SERVER_TEXT}"]})

    assert_clean(str(caught.value))
    assert_clean(logs())


# ---------- controls: what stays as it was ----------


async def test_a_working_mailbox_still_reports_connected(tmp_path, monkeypatch):
    monkeypatch.setattr(imap_module, "MailBox", WireServer().mailbox_class())
    result = await call(tmp_path, "mail_status", {})
    assert result["connected"] is True and result["detail"] is None
    assert not result.get("error")


async def test_our_own_not_found_message_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(imap_module, "MailBox", WireServer().mailbox_class())
    with pytest.raises(ToolError) as caught:
        await call(tmp_path, "mail_get", {"uid": "9"})
    assert "No message with uid 9 in INBOX." in str(caught.value)


# ---------- SMTP ----------


def fake_smtp(monkeypatch, failure: Exception, *, at: str = "send_message") -> None:
    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def starttls(self):
            pass

        def login(self, user, password):
            if at == "login":
                raise failure

        def send_message(self, msg, to_addrs=None):
            if at == "send_message":
                raise failure

        def quit(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)


SMTP_FAILURES = {
    "recipients-refused": (
        smtplib.SMTPRecipientsRefused({"bob@secret.example": (550, f"5.1.1 {SERVER_TEXT}".encode())}),
        "send_message", "smtp_recipients_refused", "SMTPRecipientsRefused",
    ),
    "login-refused": (
        smtplib.SMTPAuthenticationError(535, f"5.7.8 {SERVER_TEXT}".encode()),
        "login", "smtp_login_failed", "SMTPAuthenticationError",
    ),
    "sender-refused": (
        smtplib.SMTPSenderRefused(553, f"5.1.8 {SERVER_TEXT}".encode(), ADDRESS),
        "send_message", "smtp_sender_refused", "SMTPSenderRefused",
    ),
    "connection-lost": (
        smtplib.SMTPServerDisconnected(f"Connection unexpectedly closed: {SERVER_TEXT}"),
        "send_message", "smtp_unreachable", "SMTPServerDisconnected",
    ),
    "unreachable": (
        ConnectionRefusedError(f"[Errno 61] {SERVER_TEXT}"),
        "login", "smtp_unreachable", "ConnectionRefusedError",
    ),
}
smtp_failure = pytest.mark.parametrize(
    "failure,at,code,error_class", SMTP_FAILURES.values(), ids=SMTP_FAILURES.keys()
)


@smtp_failure
async def test_a_failed_send_returns_a_clean_tool_error(
    tmp_path, monkeypatch, smtp_wire, logs, failure, at, code, error_class
):
    fake_smtp(monkeypatch, failure, at=at)

    with pytest.raises(ToolError) as caught:
        await call(tmp_path, "mail_send", SEND)

    assert_clean(str(caught.value))
    assert code in str(caught.value)
    assert_clean(logs())
    assert smtp_wire.connections == 0  # nothing was sent, so no copy was filed either


@smtp_failure
async def test_the_smtp_error_carries_no_raw_exception_and_no_chain(
    tmp_path, monkeypatch, failure, at, code, error_class
):
    fake_smtp(monkeypatch, failure, at=at)

    with pytest.raises(SendError) as caught:
        await smtp.send(make_settings(tmp_path), smtp.build_message(make_settings(tmp_path), ["a@example.test"], "s", "b"), ["a@example.test"])

    error = caught.value
    assert_clean(str(error))
    assert error.code == code
    assert error.__cause__ is None and error.__context__ is None


@smtp_failure
async def test_an_smtp_failure_is_logged_as_its_class_only(
    tmp_path, monkeypatch, smtp_wire, caplog, failure, at, code, error_class
):
    caplog.set_level(logging.DEBUG)
    fake_smtp(monkeypatch, failure, at=at)

    with pytest.raises(ToolError):
        await call(tmp_path, "mail_send", SEND)

    ours = [r for r in caplog.records if r.name == "mail_mcp.smtp" and r.levelno == logging.WARNING]
    assert len(ours) == 1
    assert error_class in ours[0].getMessage()
    assert ours[0].exc_info is None


@pytest.fixture
def smtp_wire(monkeypatch) -> WireServer:
    server = WireServer()
    monkeypatch.setattr(imap_module, "MailBox", server.mailbox_class())
    return server
