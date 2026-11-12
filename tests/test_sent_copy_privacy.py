"""A sent copy that cannot be filed must not leak recipients or server text.

imap-tools puts the server's raw answer into its exception text. The copy is filed after the
mail has gone out, so a failure is reported next to the result rather than raised, and that
report (and the log line) must carry only a stable code and an error class: no recipient
addresses, no text the IMAP server wrote. The failures here are synthetic: the recording
server answers with a marker that stands for anything sensitive a real server might say.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastmcp import Client, FastMCP
from .imap_wire import WireServer

from mail_mcp import imap as imap_module
from mail_mcp import tools
from mail_mcp.settings import Settings

pytestmark = pytest.mark.anyio

MARKER = "LEAKED-SECRET-7d41"
SERVER_TEXT = f"[ALERT] {MARKER} quota for bob@secret.example"
SEND = {
    "to": ["someone@example.test"], "cc": ["other@example.test"],
    "subject": "Test subject", "body": "Hello", "confirm": True,
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def smtp_sent(monkeypatch) -> list:
    sent: list = []

    async def fake_send(settings, msg, recipients):
        sent.append(msg)

    monkeypatch.setattr("mail_mcp.smtp.send", fake_send)
    return sent


def use(monkeypatch, server: WireServer) -> WireServer:
    monkeypatch.setattr(imap_module, "MailBox", server.mailbox_class())
    return server


async def send(tmp_path) -> dict:
    settings = Settings(
        MAILBOX_LABEL="box@example.test", BASE_URL="https://mcp.example.test",
        GITHUB_CLIENT_ID="x", GITHUB_CLIENT_SECRET="x",
        IMAP_HOST="imap.example.test", IMAP_USER="box@example.test", IMAP_PASS="x",
        SMTP_HOST="smtp.example.test", SMTP_USER="box@example.test", SMTP_PASS="x",
        ALLOWED_RECIPIENTS="@example.test", DATA_DIR=str(tmp_path), SENT_FOLDER="Sent Messages",
    )
    mcp = FastMCP("test")
    tools.register(mcp, settings)
    async with Client(mcp) as client:
        result = await client.call_tool("mail_send", SEND)
    return result.structured_content or result.data


FAILURES = {
    "append-refused": (dict(append_reply=f"NO {SERVER_TEXT}"), "MailboxAppendError"),
    "login-refused": (dict(login_reply=f"NO {SERVER_TEXT}"), "MailboxLoginError"),
}


@pytest.mark.parametrize("knobs,error_class", FAILURES.values(), ids=FAILURES.keys())
async def test_a_failed_copy_is_reported_without_server_text(
    tmp_path, monkeypatch, smtp_sent, caplog, knobs, error_class
):
    caplog.set_level(logging.DEBUG)
    use(monkeypatch, WireServer(**knobs))

    result = await send(tmp_path)

    # The mail is out and stays "sent"; only the copy is reported as not saved.
    assert result["status"] == "sent"
    assert len(smtp_sent) == 1
    copy = result["sent_copy"]
    assert copy["saved"] is False
    # Nothing the server wrote, and no recipient, comes back to the caller ...
    reported = json.dumps(copy)
    assert MARKER not in reported and "bob@secret.example" not in reported
    assert "ALERT" not in reported and "quota" not in reported
    assert "IMAP error" not in reported
    # ... only a stable code the caller can test for.
    assert copy["error"] == "sent_copy_not_saved"


@pytest.mark.parametrize("knobs,error_class", FAILURES.values(), ids=FAILURES.keys())
async def test_a_failed_copy_logs_only_the_error_class(
    tmp_path, monkeypatch, smtp_sent, caplog, knobs, error_class
):
    caplog.set_level(logging.DEBUG)
    use(monkeypatch, WireServer(**knobs))

    await send(tmp_path)

    # ... and neither does the log: no server text, no recipient, but the class is there.
    assert MARKER not in caplog.text and "bob@secret.example" not in caplog.text
    assert "quota" not in caplog.text
    for address in ("someone@example.test", "other@example.test"):
        assert address not in caplog.text
    warnings = [r for r in caplog.records if r.name == "mail_mcp.tools" and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert error_class in warnings[0].getMessage()
    assert warnings[0].exc_info is None


async def test_a_filed_copy_is_reported_as_before(tmp_path, monkeypatch, smtp_sent, caplog):
    """Control: success is untouched, and nothing is logged as a warning."""
    caplog.set_level(logging.DEBUG)
    server = use(monkeypatch, WireServer())

    result = await send(tmp_path)

    assert result["status"] == "sent"
    assert result["sent_copy"] == {"saved": True, "folder": "Sent Messages"}
    assert [c.name for c in server.named("APPEND")] == ["APPEND"]
    assert [r for r in caplog.records if r.name == "mail_mcp.tools" and r.levelno >= logging.WARNING] == []
