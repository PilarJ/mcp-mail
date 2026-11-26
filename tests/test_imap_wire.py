"""The fake socket behind imaplib must serve replies on every read path imaplib uses."""

from __future__ import annotations

from .imap_wire import WireServer, _WireSocket


def test_replies_are_readable_through_recv_and_makefile():
    # Python 3.12/3.13 imaplib reads through sock.makefile(); 3.14+ calls sock.recv() directly.
    sock = _WireSocket(WireServer())
    assert sock.recv(4096) == b"* OK IMAP4rev1 ready\r\n"

    sock = _WireSocket(WireServer())
    assert sock.makefile("rb").readline() == b"* OK IMAP4rev1 ready\r\n"
