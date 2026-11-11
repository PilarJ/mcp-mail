"""A recording IMAP server behind the real imaplib, for tests at the serialisation boundary.

The tests in `test_sent_copy_and_search.py` replace imaplib above the point where a command
becomes bytes, so they cannot see what actually goes on the wire. Here the real
`imaplib.IMAP4` and the real imap-tools `MailBox` run unchanged and only the socket is
fake: every byte `imaplib` writes is recorded and answered like a small IMAP server would.
No real mailbox, no network.

Typical use:

    wire = WireServer()
    monkeypatch.setattr(imap_module, "MailBox", wire.mailbox_class())
    ...call a tool...
    assert wire.raw == b""                       # nothing was sent, not even a login
    assert [c.name for c in wire.mutations()] == []
"""

from __future__ import annotations

import imaplib
import re
from dataclasses import dataclass

from imap_tools.mailbox import BaseMailBox

# Commands that change the mailbox. A read-only tool must never produce one.
MUTATING = {
    "STORE", "UID STORE", "EXPUNGE", "UID EXPUNGE", "COPY", "UID COPY", "MOVE", "UID MOVE",
    "APPEND", "CREATE", "DELETE", "RENAME",
}

_LITERAL = re.compile(rb"\{(\d+)\}$")


@dataclass(frozen=True)
class Command:
    tag: str
    name: str  # upper case, "UID SEARCH" for UID commands
    line: bytes  # the whole command line as received, without CRLF


class WireServer:
    """What the server saw (`raw`, `commands`) and a few knobs for how it answers."""

    def __init__(
        self,
        *,
        login_reply: str = "OK LOGIN completed",
        append_reply: str = "OK APPEND completed",
        folders: tuple[str, ...] = ("INBOX", "Sent Messages"),
    ) -> None:
        self.login_reply = login_reply
        self.append_reply = append_reply
        self.folders = folders
        self.connections = 0
        self.raw = bytearray()
        self.commands: list[Command] = []
        self._pending = bytearray()
        self._literal_left = 0
        self._literal_command: Command | None = None
        self._out = bytearray()

    # ----- what the client socket reads -----

    def greet(self) -> None:
        self.connections += 1
        # One connection at a time, as the server makes them: a new one starts clean.
        self._out = bytearray(b"* OK IMAP4rev1 ready\r\n")
        self._pending = bytearray()
        self._literal_left = 0
        self._literal_command = None

    def take_line(self) -> bytes:
        idx = self._out.find(b"\r\n")
        if idx < 0:
            return b""  # imaplib treats an empty read as a dropped connection
        line = bytes(self._out[: idx + 2])
        del self._out[: idx + 2]
        return line

    def take(self, size: int) -> bytes:
        data = bytes(self._out[:size])
        del self._out[:size]
        return data

    # ----- what the client socket writes -----

    def feed(self, data: bytes) -> None:
        self.raw += data
        self._pending += data
        while True:
            if self._literal_left:
                taken = min(self._literal_left, len(self._pending))
                del self._pending[:taken]
                self._literal_left -= taken
                if self._literal_left:
                    return
                continue
            idx = self._pending.find(b"\r\n")
            if idx < 0:
                return
            line = bytes(self._pending[:idx])
            del self._pending[: idx + 2]
            if self._literal_command is not None:  # the CRLF that ends a command with a literal
                command, self._literal_command = self._literal_command, None
                self._answer(command)
                continue
            self._handle(line)

    def _handle(self, line: bytes) -> None:
        tag, _, rest = line.partition(b" ")
        words = rest.split(b" ")
        name = words[0].decode("ascii", "replace").upper()
        if name == "UID" and len(words) > 1:
            name = "UID " + words[1].decode("ascii", "replace").upper()
        command = Command(tag.decode("ascii", "replace"), name, line)
        self.commands.append(command)
        literal = _LITERAL.search(line)
        if literal:
            self._literal_left = int(literal.group(1))
            self._literal_command = command
            self._out += b"+ go ahead\r\n"
            return
        self._answer(command)

    def _answer(self, command: Command) -> None:
        tag, name = command.tag, command.name
        if name == "CAPABILITY":
            self._out += b"* CAPABILITY IMAP4rev1\r\n"
        elif name in {"SELECT", "EXAMINE"}:
            self._out += b"* 0 EXISTS\r\n"
        elif name == "LIST":
            for folder in self.folders:
                self._out += b'* LIST (\\HasNoChildren) "." "' + folder.encode() + b'"\r\n'
        elif name == "UID SEARCH":
            self._out += b"* SEARCH\r\n"
        elif name == "LOGOUT":
            self._out += b"* BYE logging out\r\n"
        reply = {"LOGIN": self.login_reply, "APPEND": self.append_reply}.get(name, "OK done")
        self._out += f"{tag} {reply}\r\n".encode()

    # ----- assertions -----

    def mutations(self) -> list[Command]:
        return [c for c in self.commands if c.name in MUTATING]

    def named(self, name: str) -> list[Command]:
        return [c for c in self.commands if c.name == name]

    def mailbox_class(self) -> type[BaseMailBox]:
        server = self

        class WireMailBox(BaseMailBox):
            def __init__(self, host, port=993):
                super().__init__()

            def _get_mailbox_client(self):
                return _WireIMAP4(server)

        return WireMailBox


class _WireSocket:
    def __init__(self, server: WireServer) -> None:
        self._server = server
        server.greet()

    def sendall(self, data: bytes) -> None:
        self._server.feed(data)

    def makefile(self, *args, **kwargs) -> "_WireFile":
        return _WireFile(self._server)

    def settimeout(self, *args) -> None:
        pass

    def shutdown(self, *args) -> None:
        pass

    def close(self) -> None:
        pass


class _WireFile:
    def __init__(self, server: WireServer) -> None:
        self._server = server

    def readline(self, *args) -> bytes:
        return self._server.take_line()

    def read(self, size: int = -1) -> bytes:
        return self._server.take(size if size >= 0 else 1 << 20)

    def close(self) -> None:
        pass


class _WireIMAP4(imaplib.IMAP4):
    """The real imaplib state machine and command serialiser on a fake socket."""

    def __init__(self, server: WireServer) -> None:
        self._wire = server
        super().__init__("wire.test")

    def open(self, host: str = "", port: int = 143, timeout=None) -> None:
        self.host, self.port = host, port
        self.sock = _WireSocket(self._wire)
        self.file = self.sock.makefile("rb")
