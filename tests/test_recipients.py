"""The address that passes the allowlist is the address SMTP gets.

A regex that finds the first address-shaped text is not what smtplib reads. These tests go
through the MCP tools and real smtplib, and compare the RCPT TO lines it produces.
"""

from __future__ import annotations

import re

import pytest

from mail_mcp.guard import RecipientGuard

from .support import ALLOWLISTED, BODY, MAILBOX, SUBJECT, plain_message

pytestmark = pytest.mark.anyio

ATTACKER = "attacker@example.com"

AMBIGUOUS = [
    pytest.param(f'"{ALLOWLISTED}" <{ATTACKER}>', id="quoted-display-name"),
    pytest.param(f"{ALLOWLISTED} <{ATTACKER}>", id="bare-display-name"),
    pytest.param(f'"x {ALLOWLISTED} y" <{ATTACKER}>', id="address-inside-display-name"),
    pytest.param(f'"{ATTACKER}" <{ALLOWLISTED}>', id="other-address-in-display-name"),
    pytest.param(f"{ATTACKER} <{ALLOWLISTED}>", id="other-address-in-bare-display-name"),
    pytest.param(f"{ALLOWLISTED}, {ATTACKER}", id="two-addresses"),
    pytest.param(f"{ALLOWLISTED}; {ATTACKER}", id="semicolon-list"),
    pytest.param(f"{ALLOWLISTED} {ATTACKER}", id="space-separated"),
    pytest.param(f"<{ALLOWLISTED}> <{ATTACKER}>", id="two-angle-addresses"),
    pytest.param(f"<{ALLOWLISTED}> {ATTACKER}", id="text-after-the-angle"),
    pytest.param(f"{ALLOWLISTED} ({ATTACKER})", id="comment"),
    pytest.param(f"({ALLOWLISTED}) {ATTACKER}", id="comment-first"),
    pytest.param(f"undisclosed: {ATTACKER};", id="group"),
    pytest.param(f"<@example.net:{ATTACKER}>", id="source-route"),
    pytest.param(f"{ALLOWLISTED}\r\nBcc: {ATTACKER}", id="header-injection"),
    pytest.param(f"{ALLOWLISTED}\n{ATTACKER}", id="line-break"),
    pytest.param(f"{ALLOWLISTED}\x00{ATTACKER}", id="nul"),
    pytest.param(f"{ALLOWLISTED}@{ATTACKER}", id="two-at-signs"),
    pytest.param("friend＠example.net", id="fullwidth-at"),
    pytest.param("friend@exámple.net", id="non-ascii-domain"),
    pytest.param(f'"unterminated <{ALLOWLISTED}>', id="unterminated-quote"),
    pytest.param(f'"a\\" <{ALLOWLISTED}> b" <{ATTACKER}>', id="escaped-quote"),
    pytest.param("friend%example.com@example.net", id="percent-routing"),
    pytest.param("", id="empty"),
    pytest.param("   ", id="blank"),
    pytest.param("not an address", id="no-address"),
]


def rcpt(commands: list[str]) -> list[str]:
    return re.findall(r"^rcpt TO:<(.*)>$", "\n".join(commands), re.MULTILINE)


async def send(harness, to, cc=None, **extra) -> dict:
    return await harness.call("mail_send", to=to, subject=SUBJECT, body=BODY, cc=cc, confirm=True, **extra)


@pytest.mark.parametrize("recipient", AMBIGUOUS)
async def test_ambiguous_recipient_in_to_is_refused(harness, recipient):
    commands = harness.capture_envelope()
    result = await send(harness, [recipient])
    assert result["status"] == "refused", (result, commands)
    assert rcpt(commands) == []
    assert harness.log() == []


@pytest.mark.parametrize("recipient", AMBIGUOUS)
async def test_ambiguous_recipient_in_cc_is_refused(harness, recipient):
    commands = harness.capture_envelope()
    result = await send(harness, [ALLOWLISTED], cc=[recipient])
    assert result["status"] == "refused", (result, commands)
    assert rcpt(commands) == []
    assert harness.log() == []


@pytest.mark.parametrize(
    "allowed", [pytest.param("@example.net", id="domain"), pytest.param("*", id="anyone")]
)
@pytest.mark.parametrize("recipient", [f'"x@example.net" <{ATTACKER}>', f"x@example.net, {ATTACKER}"])
async def test_ambiguous_recipient_is_refused_on_a_wide_allowlist_too(make_harness, allowed, recipient):
    harness = make_harness(ALLOWED_RECIPIENTS=allowed)
    commands = harness.capture_envelope()
    result = await send(harness, [recipient])
    assert result["status"] == "refused", (result, commands)
    assert rcpt(commands) == []


@pytest.mark.parametrize(
    "recipient", [f'"{ALLOWLISTED}" <{ATTACKER}>', f"{ALLOWLISTED} <{ATTACKER}>", f"{ALLOWLISTED}, {ATTACKER}", ""]
)
def test_the_guard_itself_refuses_them(recipient):
    assert not RecipientGuard([ALLOWLISTED]).check([recipient]).allowed


@pytest.mark.parametrize(
    "recipient",
    [
        pytest.param(ALLOWLISTED, id="bare"),
        pytest.param(f"<{ALLOWLISTED}>", id="angle-only"),
        pytest.param(f"Friend <{ALLOWLISTED}>", id="name"),
        pytest.param(f"Jan Novák <{ALLOWLISTED}>", id="non-ascii-name"),
        pytest.param(f'"Novák, Jan" <{ALLOWLISTED}>', id="quoted-name-with-comma"),
        pytest.param(f"  {ALLOWLISTED}  ", id="surrounding-space"),
        pytest.param(ALLOWLISTED.upper(), id="upper-case"),
    ],
)
async def test_one_address_goes_to_policy_envelope_header_and_log_alike(harness, recipient):
    commands = harness.capture_envelope()
    result = await send(harness, [recipient])
    assert result["status"] == "sent", result
    assert result["to"] == [ALLOWLISTED]
    assert rcpt(commands) == [ALLOWLISTED]
    assert [entry["to"] for entry in harness.log()] == [ALLOWLISTED]


async def test_the_header_carries_the_same_address_as_the_envelope(harness):
    await send(harness, [f"Friend <{ALLOWLISTED}>"], cc=[f"Friend <{ALLOWLISTED}>"])
    message = harness.outbox[-1]
    assert message.recipients == [ALLOWLISTED, ALLOWLISTED]
    assert message.message["To"] == ALLOWLISTED
    assert message.message["Cc"] == ALLOWLISTED


async def test_outsider_is_still_refused_before_confirm(harness):
    commands = harness.capture_envelope()
    result = await harness.call("mail_send", to=[ATTACKER], subject=SUBJECT, body=BODY, confirm=False)
    assert result["status"] == "refused"
    assert ATTACKER in result["detail"]
    assert rcpt(commands) == []


async def test_own_address_with_a_display_name_is_still_stripped(harness):
    commands = harness.capture_envelope()
    result = await send(harness, [ALLOWLISTED], cc=[f"Me <{MAILBOX}>"])
    assert result["status"] == "sent", result
    assert rcpt(commands) == [ALLOWLISTED]


# ---------------------------------------------------------------- replies


def incoming(*headers: str) -> bytes:
    return plain_message("Dobrý den", from_=headers[0], extra_headers=headers[1:])


@pytest.mark.parametrize(
    "from_, extra, reply_all",
    [
        pytest.param(f'"{ALLOWLISTED}" <{ATTACKER}>', (), False, id="from-display-name"),
        pytest.param(ALLOWLISTED, (f'Reply-To: "{ALLOWLISTED}" <{ATTACKER}>',), False, id="reply-to-display-name"),
        pytest.param(ALLOWLISTED, (f"Reply-To: {ALLOWLISTED}, {ATTACKER}",), False, id="two-reply-to"),
        pytest.param(ALLOWLISTED, (f'Cc: "{ALLOWLISTED}" <{ATTACKER}>',), True, id="cc-display-name"),
        pytest.param(f"{ALLOWLISTED} {ATTACKER}", (), False, id="from-without-a-usable-address"),
        pytest.param("", (), False, id="empty-from"),
    ],
)
async def test_reply_cannot_reach_an_outsider(harness, from_, extra, reply_all):
    commands = harness.capture_envelope()
    stored = harness.deliver(incoming(from_, *extra))
    result = await harness.call("mail_reply", uid=str(stored.uid), body="Díky", reply_all=reply_all, confirm=True)
    assert result["status"] == "refused", (result, commands)
    assert rcpt(commands) == []


async def test_reply_to_an_allowlisted_sender_still_works(harness):
    commands = harness.capture_envelope()
    stored = harness.deliver(incoming(f"Friend <{ALLOWLISTED}>"))
    result = await harness.call("mail_reply", uid=str(stored.uid), body="Díky", confirm=True)
    assert result["status"] == "sent", result
    assert rcpt(commands) == [ALLOWLISTED]


# ---------------------------------------------------------------- the approval path


async def test_approval_recipient_with_percent_routing_is_refused(harness):
    result = await harness.call(
        "mail_request_approval", to="client%example.com@example.net", subject=SUBJECT, body=BODY
    )
    assert result["status"] == "refused", result
    assert harness.outbox == []


async def test_approved_send_uses_the_same_address_in_the_envelope(harness):
    pending = await harness.request()
    harness.approve(pending)
    commands = harness.capture_envelope()
    result = await harness.send(pending)
    assert result["status"] == "sent", result
    assert rcpt(commands) == [pending.to]
