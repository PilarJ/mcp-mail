"""A recipient is one plain address, and the address the allowlist checked is the one mail goes to.

The guard used to take the first address-like text in a recipient string. For
`"trusted@allowed.example" <attacker@evil.example>` that is the display name, so the
allowlist said yes, while smtplib (quoteaddr/parseaddr) sent RCPT TO the address in the
angle brackets. Two addresses in one entry, a CR/LF and a quoted local part
(`"trusted@allowed.example"@evil.example`, which imap-tools passes through from a
Reply-To) had the same effect. These tests pin the rule: an entry that is not exactly one
plain address is refused, a display name is dropped, and the very same bare address is
checked, put in the headers, given to SMTP as RCPT and written to the sent log.
"""

from __future__ import annotations

import smtplib

import pytest
from fastmcp import Client, FastMCP
from imap_tools import MailMessage
from .imap_wire import WireServer

from mail_mcp import imap as imap_module
from mail_mcp import tools
from mail_mcp.guard import RecipientGuard
from mail_mcp.imap import ImapClient
from mail_mcp.settings import Settings

pytestmark = pytest.mark.anyio

TRUSTED = "trusted@allowed.example"
CRAFTED = f'"{TRUSTED}" <attacker@evil.example>'
QUOTED_LOCAL = f'"{TRUSTED}"@evil.example'
ALLOWLIST = f"{TRUSTED},@partner.example"

# Entries that are not exactly one plain address. Each one names, or hides, an address
# outside the allowlist that SMTP would (or could) deliver to.
NOT_ONE_ADDRESS = {
    "display-name-looks-allowed": CRAFTED,
    "two-addresses-comma": f"{TRUSTED}, attacker@evil.example",
    "two-addresses-semicolon": f"{TRUSTED}; attacker@evil.example",
    "crlf-header-injection": f"{TRUSTED}\r\nBcc: attacker@evil.example",
    "lf-only": f"{TRUSTED}\nattacker@evil.example",
    "cr-only": f"{TRUSTED}\rattacker@evil.example",
    "group-syntax": f"friends: {TRUSTED}, attacker@evil.example;",
    "comment-hides-second-address": f"{TRUSTED} (attacker@evil.example)",
    "two-angle-addresses": f"<{TRUSTED}> <attacker@evil.example>",
    "quoted-local-part": QUOTED_LOCAL,
    "trailing-comma": f"{TRUSTED},",
    "empty": "",
    "blank": "   ",
    "no-domain-dot": "someone@localhost",
    "space-in-local-part": "some one@allowed.example",
    "nul": f"{TRUSTED}\x00attacker@evil.example",
}
not_one_address = pytest.mark.parametrize("value", NOT_ONE_ADDRESS.values(), ids=NOT_ONE_ADDRESS.keys())


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------- guard ----------


def guard(*entries: str) -> RecipientGuard:
    return RecipientGuard(list(entries))


def test_a_display_name_that_looks_allowed_does_not_admit_its_real_address():
    verdict = guard(TRUSTED).check([CRAFTED])

    assert verdict.allowed is False


def test_the_refusal_does_not_echo_the_crafted_entry():
    verdict = guard(TRUSTED).check([CRAFTED])

    assert "evil" not in verdict.reason
    assert "attacker" not in verdict.reason
    assert "not a single valid address" in verdict.reason


@not_one_address
def test_an_entry_that_is_not_one_plain_address_is_refused(value):
    assert guard(TRUSTED, "@allowed.example").check([value]).allowed is False


@not_one_address
def test_any_recipient_does_not_admit_an_entry_that_is_not_one_address(value):
    assert guard("*").check([value]).allowed is False


def test_one_bad_entry_refuses_the_whole_list():
    assert guard(TRUSTED).check([TRUSTED, CRAFTED]).allowed is False


@pytest.mark.parametrize("value", [TRUSTED, f"Trusted Person <{TRUSTED}>", f"<{TRUSTED}>", "TRUSTED@Allowed.Example"])
def test_one_allowed_address_with_or_without_a_name_is_allowed(value):
    assert guard(TRUSTED).check([value]).allowed is True


def test_a_domain_entry_still_admits_the_domain_and_only_the_domain():
    domain = guard("@partner.example")

    assert domain.check(["Someone <someone@partner.example>"]).allowed is True
    assert domain.check(["someone@sub.partner.example"]).allowed is False
    assert domain.check(["someone@partner.example.evil.example"]).allowed is False


def test_an_address_outside_the_allowlist_is_still_named_in_the_refusal():
    verdict = guard(TRUSTED).check(["Other <other@elsewhere.example>"])

    assert verdict.allowed is False
    assert verdict.rejected == ["other@elsewhere.example"]


def test_the_canonical_address_is_the_bare_lowercase_address():
    from mail_mcp.guard import canonical_address

    assert canonical_address("Trusted Person <Trusted@Allowed.Example>") == TRUSTED
    assert canonical_address(f"  {TRUSTED}  ") == TRUSTED


@not_one_address
def test_the_parser_refuses_an_entry_that_is_not_one_plain_address(value):
    from mail_mcp.guard import InvalidAddress, canonical_address

    with pytest.raises(InvalidAddress):
        canonical_address(value)


# ---------- through the tools, with SMTP faked at smtplib ----------


class SmtpRecorder:
    """What smtplib was asked to deliver: the RCPT list and the message, per connection."""

    def __init__(self) -> None:
        self.deliveries: list[tuple[list[str], object]] = []
        self.connections = 0

    def smtp_class(self):
        recorder = self

        class FakeSMTP:
            def __init__(self, host, port, timeout=None, context=None):
                recorder.connections += 1

            def starttls(self, context=None):
                pass

            def login(self, user, password):
                pass

            def send_message(self, msg, to_addrs=None):
                recorder.deliveries.append((list(to_addrs), msg))

            def quit(self):
                pass

        return FakeSMTP


@pytest.fixture
def smtp_recorder(monkeypatch) -> SmtpRecorder:
    recorder = SmtpRecorder()
    monkeypatch.setattr(smtplib, "SMTP", recorder.smtp_class())
    monkeypatch.setattr(smtplib, "SMTP_SSL", recorder.smtp_class())
    return recorder


def make_settings(tmp_path, **extra) -> Settings:
    values = dict(
        MAILBOX_LABEL="box@example.test", BASE_URL="https://mcp.example.test",
        GITHUB_CLIENT_ID="x", GITHUB_CLIENT_SECRET="x",
        IMAP_HOST="imap.example.test", IMAP_USER="box@example.test", IMAP_PASS="x",
        SMTP_HOST="smtp.example.test", SMTP_USER="box@example.test", SMTP_PASS="x",
        ALLOWED_RECIPIENTS=ALLOWLIST, DATA_DIR=str(tmp_path),
    )
    values.update(extra)
    return Settings(**values)


async def call(tmp_path, tool: str, arguments: dict, **settings_extra) -> dict:
    mcp = FastMCP("test")
    tools.register(mcp, make_settings(tmp_path, **settings_extra))
    async with Client(mcp) as client:
        result = await client.call_tool(tool, arguments)
    return result.structured_content or result.data


def send(to: list[str], cc: list[str] | None = None) -> dict:
    return {"to": to, "cc": cc, "subject": "Test subject", "body": "Hello", "confirm": True}


async def sent_log(tmp_path) -> list[dict]:
    return (await call(tmp_path, "mail_sent_log", {}))["sent"]


@not_one_address
async def test_mail_send_refuses_an_entry_that_is_not_one_address_and_sends_nothing(tmp_path, smtp_recorder, value):
    result = await call(tmp_path, "mail_send", send([value]))

    assert result["status"] == "refused"
    assert smtp_recorder.connections == 0
    assert smtp_recorder.deliveries == []
    assert await sent_log(tmp_path) == []


async def test_mail_send_refuses_the_crafted_display_name_without_echoing_it(tmp_path, smtp_recorder):
    result = await call(tmp_path, "mail_send", send([CRAFTED]))

    assert result["status"] == "refused"
    assert "evil" not in result["detail"]
    assert smtp_recorder.deliveries == []


async def test_a_crafted_cc_refuses_the_whole_send(tmp_path, smtp_recorder):
    result = await call(tmp_path, "mail_send", send([TRUSTED], cc=[CRAFTED]))

    assert result["status"] == "refused"
    assert smtp_recorder.deliveries == []


async def test_a_named_allowed_address_is_sent_to_the_bare_address_only(tmp_path, smtp_recorder):
    result = await call(tmp_path, "mail_send", send(["Trusted Person <Trusted@Allowed.Example>"], cc=["Partner <p@partner.example>"]))

    assert result["status"] == "sent"
    assert result["to"] == [TRUSTED]
    assert result["cc"] == ["p@partner.example"]
    [(rcpt, msg)] = smtp_recorder.deliveries
    assert rcpt == [TRUSTED, "p@partner.example"]
    # What smtplib actually writes after RCPT TO: for each recipient.
    assert [smtplib.quoteaddr(r) for r in rcpt] == [f"<{TRUSTED}>", "<p@partner.example>"]
    assert msg["To"] == TRUSTED
    assert msg["Cc"] == "p@partner.example"
    assert [entry["to"] for entry in await sent_log(tmp_path)] == [TRUSTED]


# ---------- mail_reply: the recipient comes from the message being answered ----------


def incoming_message(*headers: str) -> MailMessage:
    """A received message with these extra header lines, parsed by imap-tools."""
    lines = ["To: box@example.test", "Subject: Question", "Message-ID: <m1@sender.example>", *headers]
    if not any(h.lower().startswith("from:") for h in headers):
        lines.insert(0, "From: Someone <someone@sender.example>")
    return MailMessage.from_bytes(("\r\n".join(lines) + "\r\n\r\nbody\r\n").encode())


def message_with_reply_to(reply_to: str) -> MailMessage:
    return incoming_message(f"Reply-To: {reply_to}")


@pytest.fixture
def incoming(monkeypatch):
    """The message mail_reply answers, parsed by imap-tools like a fetched one."""
    box: dict[str, MailMessage] = {}

    async def get_message(self, folder, uid, mark_seen):
        return box["msg"]

    monkeypatch.setattr(ImapClient, "get_message", get_message)
    return box


REPLY = {"uid": "1", "body": "Thanks", "confirm": True}


@pytest.mark.parametrize("reply_to", [CRAFTED, QUOTED_LOCAL], ids=["display-name-looks-allowed", "quoted-local-part"])
async def test_mail_reply_refuses_a_crafted_reply_to_and_sends_nothing(tmp_path, smtp_recorder, incoming, reply_to):
    incoming["msg"] = message_with_reply_to(reply_to)

    result = await call(tmp_path, "mail_reply", REPLY)

    assert result["status"] == "refused"
    assert smtp_recorder.deliveries == []
    assert await sent_log(tmp_path) == []


async def test_mail_reply_to_a_named_allowed_reply_to_goes_to_the_bare_address(tmp_path, smtp_recorder, incoming):
    incoming["msg"] = message_with_reply_to(f"Trusted Person <{TRUSTED}>")

    result = await call(tmp_path, "mail_reply", REPLY)

    assert result["status"] == "sent"
    [(rcpt, msg)] = smtp_recorder.deliveries
    assert rcpt == [TRUSTED]
    assert msg["To"] == TRUSTED
    assert [entry["to"] for entry in await sent_log(tmp_path)] == [TRUSTED]


# ---------- surroundings: own address, repeat guard, wildcard, drafts ----------

OWN_NAMED = "Box Owner <BOX@Example.Test>"  # this mailbox (SMTP_USER box@example.test), named and in mixed case


def assert_own_address_absent(rcpt: list[str], msg) -> None:
    for value in [*rcpt, msg["To"] or "", msg["Cc"] or ""]:
        assert "box@example.test" not in value.lower()


async def test_reply_all_leaves_this_mailbox_out_and_keeps_the_allowed_partner(tmp_path, smtp_recorder, incoming):
    incoming["msg"] = incoming_message(
        f"From: Trusted Person <{TRUSTED}>",
        f"Cc: {OWN_NAMED}, Partner <p@partner.example>",
    )

    result = await call(tmp_path, "mail_reply", {**REPLY, "reply_all": True})

    assert result["status"] == "sent"
    assert result["to"] == [TRUSTED]
    assert result["cc"] == ["p@partner.example"]
    [(rcpt, msg)] = smtp_recorder.deliveries
    assert rcpt == [TRUSTED, "p@partner.example"]
    assert msg["To"] == TRUSTED
    assert msg["Cc"] == "p@partner.example"
    assert_own_address_absent(rcpt, msg)


async def test_a_named_mixed_case_own_address_in_cc_is_left_out(tmp_path, smtp_recorder):
    result = await call(tmp_path, "mail_send", send([TRUSTED], cc=[OWN_NAMED, "Partner <p@partner.example>"]))

    assert result["status"] == "sent"
    assert result["cc"] == ["p@partner.example"]
    [(rcpt, msg)] = smtp_recorder.deliveries
    assert rcpt == [TRUSTED, "p@partner.example"]
    assert_own_address_absent(rcpt, msg)


async def test_only_this_mailbox_as_recipient_sends_nothing(tmp_path, smtp_recorder):
    result = await call(tmp_path, "mail_send", send([OWN_NAMED]))

    assert result["status"] == "refused"
    assert smtp_recorder.connections == 0


@pytest.mark.parametrize("variant", [TRUSTED, "Someone Else <trusted@allowed.example>", "TRUSTED@Allowed.EXAMPLE"])
async def test_the_repeat_guard_sees_every_form_of_the_same_address(tmp_path, smtp_recorder, variant):
    first = await call(tmp_path, "mail_send", send([f"Trusted Person <{TRUSTED}>"]), DEDUP_MINUTES=60)
    assert first["status"] == "sent"

    again = await call(tmp_path, "mail_send", send([variant]), DEDUP_MINUTES=60)

    assert again["status"] == "refused"
    assert "repeat guard" in again["detail"]
    assert len(smtp_recorder.deliveries) == 1
    log = await sent_log(tmp_path)
    assert [entry["to"] for entry in log] == [TRUSTED]


async def test_a_wildcard_allowlist_sends_to_the_same_bare_lowercase_address_everywhere(tmp_path, smtp_recorder):
    result = await call(tmp_path, "mail_send", send(["Some One <Some.One@Elsewhere.Example>"]), ALLOWED_RECIPIENTS="*")

    bare = "some.one@elsewhere.example"
    assert result["status"] == "sent"
    assert result["to"] == [bare]
    [(rcpt, msg)] = smtp_recorder.deliveries
    assert rcpt == [bare]
    assert smtplib.quoteaddr(rcpt[0]) == f"<{bare}>"
    assert msg["To"] == bare
    assert [entry["to"] for entry in await sent_log(tmp_path)] == [bare]


async def test_a_draft_is_saved_unchanged_with_an_empty_allowlist_and_nothing_is_sent(tmp_path, smtp_recorder, monkeypatch):
    wire = WireServer(folders=("INBOX", "Drafts"))
    monkeypatch.setattr(imap_module, "MailBox", wire.mailbox_class())

    result = await call(
        tmp_path, "mail_save_draft",
        {"to": ["Draft Person <draft@elsewhere.example>"], "cc": ["cc@elsewhere.example"],
         "subject": "Draft subject", "body": "Draft body"},
        ALLOWED_RECIPIENTS="",
    )

    assert result["status"] == "ok"
    assert result["folder"] == "Drafts"
    assert result["to"] == ["Draft Person <draft@elsewhere.example>"]
    [append] = wire.named("APPEND")
    assert b"Drafts" in append.line and b"\\Draft" in append.line
    raw = bytes(wire.raw)
    assert b"To: Draft Person <draft@elsewhere.example>" in raw
    assert b"Cc: cc@elsewhere.example" in raw
    assert b"Subject: Draft subject" in raw
    assert b"Draft body" in raw
    assert smtp_recorder.connections == 0
    assert smtp_recorder.deliveries == []
    assert await sent_log(tmp_path) == []
