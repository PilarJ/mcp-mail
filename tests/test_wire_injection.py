"""Caller-supplied strings must never become extra IMAP commands.

imaplib joins command arguments with spaces and writes `tag NAME args CRLF` without looking
inside the arguments, so a CR or LF in a search string is a command boundary. These tests run
the real imaplib and imap-tools on a recording socket (see `imap_wire.py`) and check what
was written: a refused input must leave the wire completely silent, and ordinary searches
(diacritics included) must still work and produce exactly the commands they should.
"""

from __future__ import annotations

import re

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from imap_wire import WireServer
from mail_mcp import imap as imap_module
from mail_mcp import tools
from mail_mcp.settings import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def wire(monkeypatch) -> WireServer:
    server = WireServer()
    monkeypatch.setattr(imap_module, "MailBox", server.mailbox_class())
    return server


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


# What the review sent: one string that carries a second and third command.
ASCII_CRLF = 'ALL\r\nINJECT1 UID STORE 1:* +FLAGS.SILENT (\\Deleted)\r\nINJECT2 EXPUNGE'
UTF8_CRLF = ASCII_CRLF + '\r\nINJECT3 UID SEARCH CHARSET UTF-8 TEXT "ž"'

UNSAFE = {
    "ascii-crlf": ASCII_CRLF,
    "non-ascii-crlf": UTF8_CRLF,
    "non-ascii-lf-only": 'TEXT "ž"\nINJECT1 EXPUNGE',
    "lf-only": "ALL\nINJECT1 EXPUNGE",
    "cr-only": "ALL\rINJECT1 EXPUNGE",
    "nul": 'TEXT "a\x00b"',
    "escape": "ALL\x1bINJECT1",
    "tab": "ALL\tINJECT1",
    "delete-char": "ALL\x7fINJECT1",
    "c1-next-line": "ALL\x85INJECT1",
}
unsafe = pytest.mark.parametrize("payload", UNSAFE.values(), ids=UNSAFE.keys())


def assert_silent(wire: WireServer, result: dict) -> None:
    """Refused before any connection: no socket, no byte, no command, no mutation."""
    assert result["status"] == "refused"
    assert "control character" in result["detail"]
    assert wire.connections == 0
    assert bytes(wire.raw) == b""
    assert wire.commands == []
    assert wire.mutations() == []


# ---------- refused inputs: zero commands ----------


@unsafe
async def test_mail_search_refuses_control_characters_in_criteria(tmp_path, wire, payload):
    assert_silent(wire, await call(tmp_path, "mail_search", {"criteria": payload}))


@unsafe
async def test_mail_list_refuses_control_characters_in_subject(tmp_path, wire, payload):
    assert_silent(wire, await call(tmp_path, "mail_list", {"subject": payload}))


@unsafe
async def test_mail_list_refuses_control_characters_in_sender(tmp_path, wire, payload):
    assert_silent(wire, await call(tmp_path, "mail_list", {"sender": payload}))


@unsafe
async def test_mail_list_never_writes_a_since_with_control_characters(tmp_path, wire, payload):
    """`since` is parsed as an ISO date first, so this was never reachable; keep it that way."""
    with pytest.raises(ToolError):
        await call(tmp_path, "mail_list", {"since": payload})
    assert wire.connections == 0 and bytes(wire.raw) == b""


@unsafe
async def test_mail_get_thread_refuses_control_characters_in_message_id(tmp_path, wire, payload):
    assert_silent(wire, await call(tmp_path, "mail_get_thread", {"message_id": payload}))


# ---------- controls: inputs that are already safe because imap-tools encodes or validates them ----------
# These pass before and after the fix. They are here so a later change that stops encoding folder
# names or validating uids cannot silently reopen the same hole.


def assert_no_injection(wire: WireServer) -> None:
    """Every command on the wire carries a tag imaplib generated, and no line ends bare CR or LF."""
    assert all(re.fullmatch(r"[A-Z]{4}\d+", c.tag) for c in wire.commands), wire.commands
    assert re.search(rb"(?<!\r)\n|\r(?!\n)", bytes(wire.raw)) is None


@unsafe
async def test_control_characters_in_a_folder_name_are_encoded_not_sent_raw(tmp_path, wire, payload):
    await call(tmp_path, "mail_list", {"folder": payload})
    await call(tmp_path, "mail_search", {"criteria": "ALL", "folder": payload})
    assert_no_injection(wire)
    assert wire.mutations() == []


@unsafe
async def test_control_characters_in_a_move_destination_are_encoded_not_sent_raw(tmp_path, wire, payload):
    await call(tmp_path, "mail_move", {"uids": ["1"], "destination": payload, "confirm": True})
    assert_no_injection(wire)


@unsafe
async def test_uids_with_control_characters_are_rejected_by_imap_tools_before_the_write(tmp_path, wire, payload):
    with pytest.raises(ToolError):
        await call(tmp_path, "mail_mark", {"uids": [f"1{payload}"]})
    assert_no_injection(wire)
    assert wire.mutations() == []


async def test_the_injected_commands_do_not_reach_a_delete_that_is_switched_off(tmp_path, wire):
    """The review's scenario end to end: read-only search, delete flag off, nothing may change."""
    result = await call(tmp_path, "mail_search", {"criteria": ASCII_CRLF})
    assert result["status"] == "refused"
    assert b"INJECT" not in bytes(wire.raw)
    assert [c for c in wire.commands if c.name in {"UID STORE", "EXPUNGE"}] == []


# ---------- controls: ordinary searches still work, with exactly the commands they should ----------


def search_commands(wire: WireServer):
    return [c.name for c in wire.commands if c.name not in {"CAPABILITY", "LOGIN", "SELECT", "LOGOUT"}]


async def test_a_diacritic_search_still_goes_out_as_utf8(tmp_path, wire):
    result = await call(tmp_path, "mail_search", {"criteria": 'TEXT "účet"'})
    assert result["count"] == 0
    (search,) = wire.named("UID SEARCH")
    assert b"SEARCH CHARSET UTF-8 " in search.line
    assert 'TEXT "účet"'.encode("utf-8") in search.line
    assert search_commands(wire) == ["UID SEARCH"]
    assert wire.mutations() == []


async def test_a_diacritic_subject_and_sender_still_go_out_as_utf8(tmp_path, wire):
    await call(tmp_path, "mail_list", {"subject": "Nabídka", "sender": "jiří@example.test"})
    (search,) = wire.named("UID SEARCH")
    assert b"SEARCH CHARSET UTF-8 " in search.line
    assert "Nabídka".encode("utf-8") in search.line and "jiří@example.test".encode("utf-8") in search.line
    assert wire.mutations() == []


async def test_an_ascii_search_is_still_us_ascii(tmp_path, wire):
    await call(tmp_path, "mail_list", {"subject": "invoice", "since": "2026-11-01"})
    (search,) = wire.named("UID SEARCH")
    assert b"SEARCH CHARSET US-ASCII " in search.line
    assert b'SUBJECT "invoice"' in search.line and b"SINCE 1-Nov-2026" in search.line


async def test_a_quote_in_a_list_filter_stays_inside_its_quoted_string(tmp_path, wire):
    await call(tmp_path, "mail_list", {"subject": 'a" OR ALL "b'})
    (search,) = wire.named("UID SEARCH")
    assert b'SUBJECT "a\\" OR ALL \\"b"' in search.line
    assert wire.mutations() == []


async def test_a_folder_with_diacritics_is_still_selected(tmp_path, wire):
    await call(tmp_path, "mail_list", {"folder": "Odeslané"})
    selected = [c.line for c in wire.named("SELECT")]
    assert any(b'SELECT "Odeslan&AOk-"' in line for line in selected)  # modified UTF-7, as imap-tools does


async def test_a_thread_lookup_by_message_id_still_works(tmp_path, wire):
    await call(tmp_path, "mail_get_thread", {"message_id": "<abc.123@example.test>"})
    searches = wire.named("UID SEARCH")
    assert len(searches) == 2
    assert all(b"<abc.123@example.test>" in c.line for c in searches)
    assert wire.mutations() == []


async def test_a_message_id_cannot_break_out_of_its_quoted_string(tmp_path, wire):
    await call(tmp_path, "mail_get_thread", {"message_id": '<a@b>" OR ALL "'})
    for search in wire.named("UID SEARCH"):
        assert b'\\" OR ALL \\"' in search.line  # escaped, so it is still one string
        assert re.search(rb'"<a@b>" OR ALL "', search.line) is None
