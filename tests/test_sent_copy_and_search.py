"""Sent copies, UTF-8 search and the date of mail without a Date header.

Nothing here talks to a real server. `FakeMailBox` is the real imap-tools mailbox class on
top of a tiny imaplib stand-in, so imap-tools itself builds the commands (the ASCII
encoding of search criteria happens inside imap-tools and is reproduced as is). SMTP is
replaced by a recorder.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest
from fastmcp import Client, FastMCP
from imap_tools.mailbox import BaseMailBox

from mail_mcp import imap as imap_module
from mail_mcp import tools
from mail_mcp.imap import ImapClient
from mail_mcp.settings import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class World:
    """What the fake server holds and what it was asked to do."""

    def __init__(self) -> None:
        self.folders = ['(\\HasNoChildren) "." "Sent Messages"', '(\\HasNoChildren) "." Odeslane', '(\\HasNoChildren) "." INBOX']
        self.messages: dict[str, bytes] = {}
        self.internal_dates: dict[str, str] = {}
        self.search_result: list[str] = []
        self.append_status = "OK"
        self.commands: list[tuple] = []
        self.uid_calls: list[tuple] = []
        self.appended: list[dict] = []


def _text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


class FakeIMAP:
    """Just enough of imaplib for the imap-tools calls this server makes."""

    def __init__(self, world: World) -> None:
        self.w = world
        self.state = "NONAUTH"
        self._pattern = "*"

    def _quote(self, value: str) -> str:
        return '"' + value + '"'

    def _simple_command(self, name, *args):
        self.w.commands.append((name, args))
        if name == "LIST":
            self._pattern = _text(args[1]).strip('"')
        return ("OK", [b"done"])

    def _untagged_response(self, typ, data, name):
        names = [line for line in self.w.folders]
        if self._pattern != "*":
            names = [line for line in names if _text(line).strip().strip('"').endswith(self._pattern) or f'"{self._pattern}"' in line]
        return ("OK", [line.encode() for line in names])

    def select(self, folder, readonly=False):
        self.w.commands.append(("SELECT", (folder, readonly)))
        return ("OK", [b"0"])

    def uid(self, command, *args):
        self.w.uid_calls.append((command.upper(), args))
        if command.upper() == "SEARCH":
            return ("OK", [" ".join(self.w.search_result).encode()])
        if command.upper() == "FETCH":
            uids, parts = args[0], args[1]
            if "INTERNALDATE" in parts and "BODY" not in parts:
                lines = [
                    f'1 (UID {u} INTERNALDATE "{self.w.internal_dates[u]}")'.encode()
                    for u in uids.split(",")
                    if u in self.w.internal_dates
                ]
                return ("OK", lines)
            items: list = []
            for uid in uids.split(","):
                raw = self.w.messages[uid]
                head = f"1 (UID {uid} FLAGS (\\Seen) RFC822.SIZE {len(raw)} BODY[] {{{len(raw)}}}".encode()
                items += [(head, raw), b")"]
            return ("OK", items)
        raise AssertionError(f"unexpected UID command {command}")

    def append(self, folder, flags, date, message):
        self.w.appended.append({"folder": _text(folder).strip('"'), "flags": flags, "date": date, "message": message})
        return (self.w.append_status, [b"appended"])

    def logout(self):
        return ("BYE", [b""])


@pytest.fixture
def world(monkeypatch) -> World:
    w = World()

    class FakeMailBox(BaseMailBox):
        def __init__(self, host, port=993, ssl_context=None):
            super().__init__()

        def _get_mailbox_client(self):
            return FakeIMAP(w)

    monkeypatch.setattr(imap_module, "MailBox", FakeMailBox)
    return w


def make_settings(tmp_path, **extra) -> Settings:
    values = dict(
        MAILBOX_LABEL="box@example.test", BASE_URL="https://mcp.example.test",
        GITHUB_CLIENT_ID="x", GITHUB_CLIENT_SECRET="x",
        IMAP_HOST="imap.example.test", IMAP_USER="box@example.test", IMAP_PASS="x",
        SMTP_HOST="smtp.example.test", SMTP_USER="box@example.test", SMTP_PASS="x",
        ALLOWED_RECIPIENTS="@example.test", DATA_DIR=str(tmp_path),
    )
    values.update(extra)
    return Settings(**values)


@pytest.fixture
def smtp_sent(monkeypatch) -> list[EmailMessage]:
    sent: list[EmailMessage] = []

    async def fake_send(settings, msg, recipients):
        sent.append(msg)

    monkeypatch.setattr("mail_mcp.smtp.send", fake_send)
    return sent


async def call(tmp_path, tool: str, arguments: dict, **settings_extra) -> dict:
    mcp = FastMCP("test")
    tools.register(mcp, make_settings(tmp_path, **settings_extra))
    async with Client(mcp) as client:
        result = await client.call_tool(tool, arguments)
    return result.structured_content or result.data


SEND = {"to": ["someone@example.test"], "subject": "Test subject", "body": "Hello", "confirm": True}


def raw_message(subject: str, with_date: bool) -> bytes:
    lines = ["From: a@example.test", "To: box@example.test", f"Subject: {subject}", "Message-ID: <m@example.test>"]
    if with_date:
        lines.append("Date: Tue, 6 Oct 2026 08:10:50 +0200")
    return ("\r\n".join(lines) + "\r\n\r\nbody\r\n").encode()


# ---------- search with diacritics ----------


async def test_a_subject_with_diacritics_is_searched_as_utf8(world):
    client = ImapClient(make_settings("/tmp"))
    await client.list_messages("INBOX", 5, False, None, None, "Nabídka")
    search = [args for cmd, args in world.uid_calls if cmd == "SEARCH"]
    assert len(search) == 1
    assert search[0][:2] == ("CHARSET", "UTF-8")
    assert "Nabídka".encode("utf-8") in search[0][2]


async def test_a_raw_search_with_diacritics_is_searched_as_utf8(world):
    client = ImapClient(make_settings("/tmp"))
    await client.search_raw("INBOX", 'TEXT "účet"', 5)
    search = [args for cmd, args in world.uid_calls if cmd == "SEARCH"]
    assert search[0][:2] == ("CHARSET", "UTF-8")


async def test_an_ascii_search_is_unchanged(world):
    client = ImapClient(make_settings("/tmp"))
    await client.list_messages("INBOX", 5, False, None, None, "invoice")
    search = [args for cmd, args in world.uid_calls if cmd == "SEARCH"]
    assert search[0][:2] == ("CHARSET", "US-ASCII")


# ---------- copy of sent mail ----------


async def test_without_sent_folder_no_copy_is_filed(tmp_path, world, smtp_sent):
    # Opt-in: even with a folder the server marks \Sent, nothing is filed unless asked for.
    world.folders.append('(\\HasNoChildren \\Sent) "." Gesendet')
    result = await call(tmp_path, "mail_send", SEND)
    assert result["status"] == "sent"
    assert len(smtp_sent) == 1
    assert world.appended == []
    assert "sent_copy" not in result


async def test_mail_send_saves_a_copy_in_sent_folder(tmp_path, world, smtp_sent):
    result = await call(tmp_path, "mail_send", SEND, SENT_FOLDER="Odeslane")
    assert result["status"] == "sent"
    assert len(smtp_sent) == 1
    assert len(world.appended) == 1
    copy = world.appended[0]
    assert copy["folder"] == "Odeslane"
    assert "\\Seen" in copy["flags"]
    assert abs(copy["date"] - datetime.now(timezone.utc)) < timedelta(minutes=1)
    assert f"Message-ID: {smtp_sent[0]['Message-ID']}".encode() in copy["message"]
    assert b"Subject: Test subject" in copy["message"]
    assert re.search(rb"(?m)^Date: .+", copy["message"])
    assert result["sent_copy"] == {"saved": True, "folder": "Odeslane"}


async def test_a_missing_sent_folder_is_not_created(tmp_path, world, smtp_sent, caplog):
    # A typo, or the webmail's translated name of "Sent", must not quietly create a new folder.
    result = await call(tmp_path, "mail_send", SEND, SENT_FOLDER="Neexistuje")
    assert "sent_folder_missing" in caplog.text
    assert result["status"] == "sent"
    assert world.appended == []
    assert not [name for name, _ in world.commands if name == "CREATE"]
    assert result["sent_copy"]["saved"] is False


async def test_a_failed_copy_does_not_turn_a_sent_mail_into_a_failure(tmp_path, world, smtp_sent):
    world.append_status = "NO"
    result = await call(tmp_path, "mail_send", SEND, SENT_FOLDER="Odeslane")
    assert result["status"] == "sent"
    assert len(smtp_sent) == 1
    assert result["sent_copy"]["saved"] is False


async def test_a_refused_send_saves_no_copy(tmp_path, world, smtp_sent):
    result = await call(tmp_path, "mail_send", {**SEND, "confirm": False}, SENT_FOLDER="Odeslane")
    assert result["status"] == "refused"
    assert smtp_sent == [] and world.appended == []


async def test_mail_status_reports_the_sent_folder(tmp_path, world):
    on = await call(tmp_path, "mail_status", {}, SENT_FOLDER="Odeslane")
    assert on["sent_copy"] == {"folder": "Odeslane", "exists": True}
    missing = await call(tmp_path, "mail_status", {}, SENT_FOLDER="Neexistuje")
    assert missing["sent_copy"] == {"folder": "Neexistuje", "exists": False}
    off = await call(tmp_path, "mail_status", {})
    assert off["sent_copy"] is None


async def test_mail_reply_saves_a_copy_too(tmp_path, world, smtp_sent):
    world.search_result = ["7"]
    world.messages["7"] = raw_message("Question", with_date=True)
    result = await call(tmp_path, "mail_reply", {"uid": "7", "body": "Answer", "confirm": True}, SENT_FOLDER="Odeslane")
    assert result["status"] == "sent"
    assert len(world.appended) == 1
    assert b"Subject: Re: Question" in world.appended[0]["message"]


# ---------- date of mail without a Date header ----------


async def test_mail_without_a_date_header_shows_the_delivery_date(tmp_path, world):
    world.search_result = ["396", "397"]
    world.messages.update({"396": raw_message("No date", False), "397": raw_message("Has date", True)})
    world.internal_dates["396"] = "14-Sep-2026 11:00:20 +0200"
    result = await call(tmp_path, "mail_list", {"folder": "Odeslane"})
    by_uid = {m["uid"]: m for m in result["messages"]}
    assert by_uid["396"]["date"] == "2026-09-14T11:00:20+02:00"
    assert by_uid["396"]["date_missing"] is True
    assert by_uid["397"]["date"].startswith("2026-10-06T08:10:50")
    assert not by_uid["397"].get("date_missing")
    asked = [args for cmd, args in world.uid_calls if cmd == "FETCH" and "INTERNALDATE" in args[1]]
    assert len(asked) == 1 and asked[0][0] == "396"


async def test_no_extra_request_when_every_message_has_a_date(tmp_path, world):
    world.search_result = ["397"]
    world.messages["397"] = raw_message("Has date", True)
    await call(tmp_path, "mail_list", {"folder": "Odeslane"})
    assert not [1 for cmd, args in world.uid_calls if cmd == "FETCH" and "INTERNALDATE" in args[1]]
