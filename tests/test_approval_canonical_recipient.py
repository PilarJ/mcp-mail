"""The approval path reads a recipient exactly as mail_send does (canonical_address).

What the approver is shown, what the approval fingerprint covers, the SMTP envelope, the
To header, the sent log and the tool result must all be one and the same bare address. A
recipient that canonical_address would refuse, or would rewrite (display name, case), is
refused when approval is requested, so no approval can exist for it.
"""

from __future__ import annotations

import sqlite3

import pytest

from mail_mcp.approval import fingerprint

from .support import ALLOWLISTED, APPROVER, BODY, RECIPIENT, SUBJECT, Pending

pytestmark = pytest.mark.anyio

ATTACKER = "attacker@evil.example"
SPOOF = f'"{RECIPIENT}" <{ATTACKER}>'


def rcpt(commands: list[str]) -> list[str]:
    return [c for c in commands if c.lower().startswith("rcpt")]


def assert_nothing_happened(harness) -> None:
    assert harness.outbox == []
    assert harness.log() == []


# ---------------------------------------------------------------- the request


@pytest.mark.parametrize(
    "to",
    [
        pytest.param('"trusted@allowed.example" <attacker@evil.example>', id="quoted-display-name-spoof"),
        pytest.param(SPOOF, id="spoof-of-the-real-recipient"),
        pytest.param(f"{RECIPIENT} <{ATTACKER}>", id="unquoted-display-name-spoof"),
        pytest.param(f"Client <{RECIPIENT}>", id="display-name"),
        pytest.param(f'"{RECIPIENT}"@evil.example', id="quoted-local-part"),
        pytest.param(f"{RECIPIENT} ({ATTACKER})", id="comment"),
        pytest.param(f"{RECIPIENT}\r\nBcc: {ATTACKER}", id="crlf"),
        pytest.param(f" {RECIPIENT}", id="leading-space"),
        pytest.param("Client@Example.Net", id="mixed-case"),
        pytest.param("client..x@example.net", id="double-dot"),
        pytest.param(".client@example.net", id="leading-dot"),
        pytest.param("client.@example.net", id="trailing-dot"),
        pytest.param("client@-example.net", id="label-starts-with-hyphen"),
        pytest.param("client@example-.net", id="label-ends-with-hyphen"),
    ],
)
async def test_a_recipient_canonical_address_refuses_or_rewrites_gets_no_approval(harness, to):
    result = await harness.call("mail_request_approval", to=to, subject=SUBJECT, body=BODY)
    assert result["status"] == "refused", result
    assert harness.to_approver() == []
    assert_nothing_happened(harness)


async def test_the_refusal_does_not_repeat_the_recipient(harness):
    result = await harness.call("mail_request_approval", to=SPOOF, subject=SUBJECT, body=BODY)
    assert result["status"] == "refused"
    assert ATTACKER not in result["detail"] and RECIPIENT not in result["detail"]


# ---------------------------------------------------------------- one address everywhere


async def test_the_approved_address_is_the_one_shown_fingerprinted_sent_and_logged(harness):
    requested = await harness.call("mail_request_approval", to=RECIPIENT, subject=SUBJECT, body=BODY)
    assert requested["status"] == "approval_requested", requested
    assert requested["to"] == RECIPIENT

    # The approver is shown exactly this address, at the top and again after the body.
    [request] = harness.to_approver()
    shown = [line for line in request.text.splitlines() if line.startswith("Příjemce")]
    assert shown == [f"Příjemce: {RECIPIENT}", f"Příjemce (znovu): {RECIPIENT}"]
    # The approval record covers this address and nothing else.
    assert stored_fingerprint(harness, requested["approval_id"]) == fingerprint(RECIPIENT, SUBJECT, BODY)

    pending = Pending(requested["approval_id"], harness.code_from_last_request(), RECIPIENT, SUBJECT, BODY)
    harness.approve(pending)
    sent = await harness.send(pending)
    assert sent["status"] == "sent", sent
    assert sent["to"] == RECIPIENT
    [message] = harness.delivered()
    assert message.recipients == [RECIPIENT]  # what smtplib is given for RCPT TO
    assert message.message["To"] == RECIPIENT
    assert message.message["Cc"] is None
    assert [entry["to"] for entry in harness.log()] == [RECIPIENT] * 3
    assert harness.log_kinds() == ["approval_requested", "approved", "sent_approved"]


async def test_the_real_envelope_of_an_approved_send_has_one_rcpt(harness):
    pending = await harness.request()
    harness.approve(pending)
    commands = harness.capture_envelope()
    assert (await harness.send(pending))["status"] == "sent"
    assert rcpt(commands) == [f"rcpt TO:<{RECIPIENT}>"]


# ---------------------------------------------------------------- sending something else


@pytest.mark.parametrize(
    "to",
    [
        pytest.param(SPOOF, id="spoof-of-the-approved-recipient"),
        pytest.param(f"Client <{RECIPIENT}>", id="display-name"),
        pytest.param(f"{RECIPIENT}\r\nBcc: {ATTACKER}", id="crlf"),
    ],
)
async def test_an_approved_send_to_a_rewritten_recipient_voids_and_logs_no_raw_text(harness, to):
    pending = await harness.request()
    harness.approve(pending)

    result = await harness.send(pending, to=to)
    assert result["status"] == "refused", result
    assert harness.delivered() == []
    # The audit log must not credit the void to an address the text only pretends to be.
    voided = [entry for entry in harness.log() if entry["kind"] == "approval_voided"]
    assert len(voided) == 1
    assert voided[0]["to"] == ""
    # And the approval is gone for the original too.
    assert (await harness.send(pending))["status"] == "refused"
    assert harness.delivered() == []


async def test_an_approved_send_to_another_valid_address_logs_that_address(harness):
    pending = await harness.request()
    harness.approve(pending)
    result = await harness.send(pending, to=ALLOWLISTED)
    assert result["status"] == "refused"
    voided = [entry for entry in harness.log() if entry["kind"] == "approval_voided"]
    assert [entry["to"] for entry in voided] == [ALLOWLISTED]


async def test_mail_send_with_approval_enabled_still_refuses_the_spoof(make_harness):
    harness = make_harness(APPROVER_ADDRESS=APPROVER, ALLOWED_RECIPIENTS=RECIPIENT)
    commands = harness.capture_envelope()
    result = await harness.call("mail_send", to=[SPOOF], subject=SUBJECT, body=BODY, confirm=True)
    assert result["status"] == "refused", result
    assert rcpt(commands) == []
    assert harness.log() == []


# ---------------------------------------------------------------- helpers


def stored_fingerprint(harness, approval_id: str) -> str:
    with sqlite3.connect(harness.settings.approval_db_path) as db:
        [(value,)] = db.execute("SELECT fingerprint FROM approvals WHERE id = ?", (approval_id,)).fetchall()
    return value
