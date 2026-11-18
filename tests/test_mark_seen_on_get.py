"""mail_get marks a message read by default; MARK_SEEN_ON_GET and mark_seen switch it (#11).

The real imaplib and imap-tools run against `SeenTrackingServer`, which keeps the flags of
the messages it holds the way an IMAP server does (RFC 3501): a FETCH of BODY[] on a folder
selected read-write sets \\Seen, BODY.PEEK[] does not, and UID STORE changes flags. The
tests then read the server's flags, so they see what a real mailbox would show, whichever
command produced it. No real mailbox, no network.
"""

from __future__ import annotations

import re
from datetime import date
from email.message import EmailMessage

import pytest
from fastmcp import Client, FastMCP
from pydantic import ValidationError

from mail_mcp import imap as imap_module
from mail_mcp import tools
from mail_mcp.imap import ImapClient
from mail_mcp.settings import Settings, load_settings

from .imap_wire import Command, WireServer

pytestmark = pytest.mark.anyio

SEEN = "\\Seen"
MESSAGE_ID = "<m11@example.test>"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class SeenTrackingServer(WireServer):
    """WireServer that holds messages and their flags and answers SEARCH, FETCH and STORE."""

    def __init__(self, messages: dict[int, bytes]) -> None:
        super().__init__()
        self.messages = dict(messages)
        self.flags: dict[int, set[str]] = {uid: set() for uid in messages}
        self.read_write = False

    def _answer(self, command: Command) -> None:
        line = command.line.decode("ascii", "replace")
        if command.name in {"SELECT", "EXAMINE"}:
            self.read_write = command.name == "SELECT"
        elif command.name == "UID SEARCH":
            self._out += self._search(line)
            self._out += f"{command.tag} OK SEARCH completed\r\n".encode()
            return
        elif command.name == "UID FETCH":
            self._out += self._fetch(line)
            self._out += f"{command.tag} OK FETCH completed\r\n".encode()
            return
        elif command.name == "UID STORE":
            self._store(line)
        super()._answer(command)

    def _uids(self, uid_set: str) -> list[int]:
        return [int(u) for u in uid_set.split(",") if u.isdigit() and int(u) in self.messages]

    def _search(self, line: str) -> bytes:
        wanted = re.search(r"\bUID (\d+)", line.split("SEARCH", 1)[1])
        uids = self._uids(wanted.group(1)) if wanted else sorted(self.messages)
        return ("* SEARCH " + " ".join(map(str, uids))).rstrip().encode() + b"\r\n"

    def _fetch(self, line: str) -> bytes:
        uid_set, items = re.match(r"\S+ UID FETCH (\S+) (.*)$", line).groups()
        items = items.upper()
        out = bytearray()
        for seq, uid in enumerate(self._uids(uid_set), start=1):
            body = re.search(r"BODY(\.PEEK)?\[(HEADER)?\]", items)
            # RFC 3501 6.4.5: BODY[...] sets \Seen; BODY.PEEK[...] does not; nor does EXAMINE.
            if body and not body.group(1) and self.read_write:
                self.flags[uid].add(SEEN)
            meta = f"* {seq} FETCH (UID {uid} FLAGS ({' '.join(sorted(self.flags[uid]))})"
            if "INTERNALDATE" in items:
                meta += ' INTERNALDATE "06-Oct-2026 08:10:50 +0000"'
            raw = self.messages[uid]
            if "RFC822.SIZE" in items:
                meta += f" RFC822.SIZE {len(raw)}"
            if body is None:
                out += (meta + ")\r\n").encode()
                continue
            if body.group(2):  # headers only
                raw = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
            out += f"{meta} BODY[{body.group(2) or ''}] {{{len(raw)}}}\r\n".encode() + raw + b")\r\n"
        return bytes(out)

    def _store(self, line: str) -> None:
        match = re.match(r"\S+ UID STORE (\S+) ([+-])FLAGS(?:\.SILENT)? \(([^)]*)\)", line)
        for uid in self._uids(match.group(1)):
            flags = set(match.group(3).split())
            if match.group(2) == "+":
                self.flags[uid] |= flags
            else:
                self.flags[uid] -= flags


def message_with_attachment() -> bytes:
    msg = EmailMessage()
    msg["From"] = "sender@example.test"
    msg["To"] = "box@example.test"
    msg["Subject"] = "Invoice"
    msg["Date"] = "Tue, 6 Oct 2026 08:10:50 +0200"
    msg["Message-ID"] = MESSAGE_ID
    msg.set_content("Hello, the invoice is attached.")
    msg.add_attachment(b"%PDF-1.4 test", maintype="application", subtype="pdf", filename="invoice.pdf")
    return msg.as_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


@pytest.fixture
def server(monkeypatch) -> SeenTrackingServer:
    wire = SeenTrackingServer({7: message_with_attachment()})
    monkeypatch.setattr(imap_module, "MailBox", wire.mailbox_class())
    return wire


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


async def call(tmp_path, tool: str, arguments: dict, **settings_extra) -> dict:
    mcp = FastMCP("test")
    tools.register(mcp, make_settings(tmp_path, **settings_extra))
    async with Client(mcp) as client:
        result = await client.call_tool(tool, arguments)
    return result.structured_content or result.data


# ---------------------------------------------------------------- mail_get


async def test_the_server_double_marks_on_body_and_not_on_peek(server):
    """The double itself: without this, every test below could pass for the wrong reason."""
    client = ImapClient(make_settings("/tmp"))
    await client.get_message("INBOX", "7", mark_seen=False)
    assert server.flags[7] == set()
    await client.get_message("INBOX", "7", mark_seen=True)
    assert server.flags[7] == {SEEN}


async def test_mail_get_marks_the_message_read_by_default(tmp_path, server):
    result = await call(tmp_path, "mail_get", {"uid": "7"})
    assert result["subject"] == "Invoice"
    assert server.flags[7] == {SEEN}


async def test_mail_get_marks_read_when_the_setting_is_true(tmp_path, server):
    await call(tmp_path, "mail_get", {"uid": "7"}, MARK_SEEN_ON_GET="true")
    assert server.flags[7] == {SEEN}


async def test_setting_false_leaves_the_message_unread(tmp_path, server):
    result = await call(tmp_path, "mail_get", {"uid": "7"}, MARK_SEEN_ON_GET="false")
    assert result["subject"] == "Invoice"
    assert server.flags[7] == set()
    assert not server.mutations()


async def test_mark_seen_false_overrides_the_default_for_one_call(tmp_path, server):
    await call(tmp_path, "mail_get", {"uid": "7", "mark_seen": False})
    assert server.flags[7] == set()
    await call(tmp_path, "mail_get", {"uid": "7", "mark_seen": False}, MARK_SEEN_ON_GET="true")
    assert server.flags[7] == set()


async def test_mark_seen_true_overrides_a_false_setting_for_one_call(tmp_path, server):
    await call(tmp_path, "mail_get", {"uid": "7", "mark_seen": True}, MARK_SEEN_ON_GET="false")
    assert server.flags[7] == {SEEN}


async def test_mark_seen_null_uses_the_instance_setting(tmp_path, server):
    await call(tmp_path, "mail_get", {"uid": "7", "mark_seen": None}, MARK_SEEN_ON_GET="false")
    assert server.flags[7] == set()
    await call(tmp_path, "mail_get", {"uid": "7", "mark_seen": None})
    assert server.flags[7] == {SEEN}


@pytest.mark.parametrize(("setting", "shown"), [("true", "marks it read"), ("false", "does not mark it read")])
async def test_the_tool_description_states_the_instance_default(tmp_path, setting, shown):
    mcp = FastMCP("test")
    tools.register(mcp, make_settings(tmp_path, MARK_SEEN_ON_GET=setting))
    async with Client(mcp) as client:
        listed = {tool.name: tool for tool in await client.list_tools()}
    description = listed["mail_get"].description
    assert f"this mailbox {shown}" in description
    assert "mark_seen" in description
    schema = listed["mail_get"].inputSchema["properties"]["mark_seen"]
    assert "MARK_SEEN_ON_GET" in schema["description"]


# ---------------------------------------------------------------- nothing else marks


@pytest.mark.parametrize("setting", ["true", "false"])
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("mail_list", {}),
        ("mail_list", {"unread_only": True}),
        ("mail_search", {"criteria": 'TEXT "invoice"'}),
        ("mail_get_thread", {"message_id": MESSAGE_ID}),
        ("mail_get_attachment", {"uid": "7", "filename": "invoice.pdf"}),
    ],
)
async def test_other_read_tools_never_mark_read(tmp_path, server, setting, tool, arguments):
    result = await call(tmp_path, tool, arguments, MARK_SEEN_ON_GET=setting)
    assert result.get("status") != "refused", result
    if tool == "mail_get_attachment":
        assert result["filename"] == "invoice.pdf"
    else:
        assert result["count"] >= 1
    assert server.flags[7] == set()
    assert not server.mutations()
    fetches = [c.line.upper() for c in server.named("UID FETCH")]
    assert fetches and not any(b"BODY[" in f for f in fetches), fetches


async def test_approval_check_never_marks_read(tmp_path, server):
    client = ImapClient(make_settings(tmp_path, MARK_SEEN_ON_GET="true"))
    found = await client.approval_candidates("sender@example.test", date(2026, 10, 1), 10, 1_000_000)
    assert [m.uid for m in found] == ["7"]
    assert server.flags[7] == set()
    assert not server.mutations()


# ---------------------------------------------------------------- the setting


def test_the_setting_defaults_to_true(tmp_path):
    assert make_settings(tmp_path).mark_seen_on_get is True


@pytest.mark.parametrize(("raw", "value"), [("true", True), ("false", False), ("0", False), ("1", True), ("", True)])
def test_the_setting_reads_booleans_and_empty_means_default(tmp_path, raw, value):
    assert make_settings(tmp_path, MARK_SEEN_ON_GET=raw).mark_seen_on_get is value


@pytest.mark.parametrize("raw", ["maybe", "2", "truee", "none"])
def test_an_invalid_setting_stops_the_server_at_start(tmp_path, monkeypatch, raw):
    monkeypatch.chdir(tmp_path)  # no .env from the working directory
    for name, value in {
        "MAILBOX_LABEL": "box@example.test", "BASE_URL": "https://mcp.example.test",
        "GITHUB_CLIENT_ID": "x", "GITHUB_CLIENT_SECRET": "x",
        "IMAP_HOST": "imap.example.test", "IMAP_USER": "box@example.test", "IMAP_PASS": "x",
        "SMTP_HOST": "smtp.example.test", "SMTP_USER": "box@example.test", "SMTP_PASS": "x",
        "DATA_DIR": str(tmp_path),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("MARK_SEEN_ON_GET", "false")
    assert load_settings().mark_seen_on_get is False  # the environment is what is read
    monkeypatch.setenv("MARK_SEEN_ON_GET", raw)
    with pytest.raises(ValidationError, match="MARK_SEEN_ON_GET"):
        load_settings()
