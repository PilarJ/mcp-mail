"""Approval sending together with what main added since: sent copies and error codes.

An approved message is sent mail like any other, so SENT_FOLDER files a copy of it too.
Failures are logged by the stable class name the error carries; main no longer chains the
library exception, so a log line built from `__cause__` would only say "NoneType".
"""
from __future__ import annotations

import logging
import smtplib

import pytest

from mail_mcp import imap as imap_module

from .support import APPROVER, RECIPIENT, SUBJECT, Harness

pytestmark = pytest.mark.anyio


@pytest.fixture
def filing_harness(tmp_path, monkeypatch, keys) -> Harness:
    harness = Harness(tmp_path, monkeypatch, keys, APPROVER_ADDRESS=APPROVER, SENT_FOLDER="Sent")
    harness.imap.folders["Sent"] = []
    return harness


async def test_an_approved_message_is_filed_in_the_sent_folder(filing_harness):
    harness = filing_harness
    pending = await harness.request()
    harness.approve(pending)
    result = await harness.send(pending)
    assert result["status"] == "sent", result
    assert result["sent_copy"] == {"saved": True, "folder": "Sent"}
    filed = harness.imap.folders["Sent"]
    assert len(filed) == 1
    assert filed[0].flags == ("\\Seen",)
    assert harness.delivered()[0].message.as_bytes() == filed[0].raw


async def test_a_missing_sent_folder_does_not_turn_an_approved_send_into_a_failure(
    tmp_path, monkeypatch, keys
):
    harness = Harness(tmp_path, monkeypatch, keys, APPROVER_ADDRESS=APPROVER, SENT_FOLDER="Sent")
    pending = await harness.request()
    harness.approve(pending)
    result = await harness.send(pending)
    assert result["status"] == "sent", result
    assert result["sent_copy"]["saved"] is False
    assert result["sent_copy"]["error"] == "sent_copy_not_saved"
    assert "Sent" not in harness.imap.folders  # never created
    assert len(harness.delivered()) == 1


async def test_without_sent_folder_an_approved_send_reports_no_copy(harness):
    pending = await harness.request()
    harness.approve(pending)
    result = await harness.send(pending)
    assert result["status"] == "sent", result
    assert "sent_copy" not in result


async def test_a_failed_approval_search_is_logged_by_its_class(harness, monkeypatch, caplog):
    pending = await harness.request()
    harness.approve(pending)

    async def unreachable(*_args, **_kwargs):
        raise imap_module.imap_failure(ConnectionRefusedError(f"refused for {APPROVER}"))

    monkeypatch.setattr(imap_module.ImapClient, "approval_candidates", unreachable)
    with caplog.at_level(logging.WARNING, logger="mail_mcp"):
        result = await harness.send(pending)
    assert result["status"] == "refused"
    lines = [r.getMessage() for r in caplog.records if "Approval search failed" in r.getMessage()]
    assert lines == ["Approval search failed: ConnectionRefusedError"]
    assert APPROVER not in caplog.text


async def test_a_failed_approved_send_is_logged_by_its_class(harness, caplog):
    pending = await harness.request()
    harness.approve(pending)
    harness.smtp_failure["error"] = smtplib.SMTPRecipientsRefused({RECIPIENT: (550, b"5.1.1 no")})
    with caplog.at_level(logging.WARNING, logger="mail_mcp"):
        result = await harness.send(pending)
    assert result["status"] == "refused"
    lines = [r.getMessage() for r in caplog.records if "Approved message not sent" in r.getMessage()]
    assert lines == ["Approved message not sent: SMTPRecipientsRefused"]
    assert RECIPIENT not in caplog.text
    assert SUBJECT not in caplog.text
