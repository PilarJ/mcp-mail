"""Fake IMAP and SMTP, DKIM signing with a test key, and a harness around the MCP tools.

Nothing here touches the network. The fakes are deliberately dumb: the fake IMAP search
returns every message in the selected folder whatever the criteria say, so the server's
own checks do the refusing and not a helpful search filter.
"""

from __future__ import annotations

import base64
import email
import json
import quopri
import re
import time
from dataclasses import dataclass, field
from email import policy
from pathlib import Path
from types import SimpleNamespace

import dkim
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dkim.canonicalization import CanonicalizationPolicy
from dkim.crypto import HASH_ALGORITHMS, parse_pem_private_key
from fastmcp import Client, FastMCP
from imap_tools import MailMessage

from mail_mcp import tools
from mail_mcp.settings import Settings

APPROVER = "approver@example.com"
MAILBOX = "info@example.org"
RECIPIENT = "client@example.net"
ALLOWLISTED = "friend@example.net"
SUBJECT = "Nabídka"
BODY = "Dobrý den,\nposílám nabídku.\n\nS pozdravem"

REAL_REPLY_HEADERS = ("From", "Subject", "Date", "To", "References", "In-Reply-To")
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


# ---------------------------------------------------------------- DKIM test keys


@dataclass(frozen=True)
class TestKey:
    private_pem: bytes
    public_b64: bytes

    def txt(self, extra: bytes = b"") -> bytes:
        return b"v=DKIM1; k=rsa; " + extra + b"p=" + self.public_b64


def generate_key() -> TestKey:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    public_der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return TestKey(private_pem, base64.b64encode(public_der))


def dkim_sign(
    raw: bytes,
    key: TestKey,
    *,
    domain: bytes = b"example.com",
    selector: bytes = b"sel",
    algorithm: bytes = b"rsa-sha256",
    headers: tuple[str, ...] = REAL_REPLY_HEADERS,
    canonicalization: bytes = b"simple/simple",
    expires: int | None = None,
    signed_at: int | None = None,
    body_length: bool = False,
) -> bytes:
    """Prepend a signature. Built by hand because dkim.sign refuses the bad ones we need."""
    signer = dkim.DKIM(raw, signature_algorithm=algorithm)
    signer.hasher = HASH_ALGORITHMS[algorithm]
    canon = CanonicalizationPolicy.from_c_value(canonicalization)
    body = canon.canonicalize_body(signer.body)
    digest = signer.hasher()
    digest.update(body)
    include = tuple(h.lower().encode() for h in headers)
    fields = [
        (b"v", b"1"),
        (b"a", algorithm),
        (b"c", canonicalization),
        (b"d", domain),
        (b"s", selector),
        (b"t", str(signed_at if signed_at is not None else int(time.time())).encode()),
    ]
    if expires is not None:
        fields.append((b"x", str(expires).encode()))
    if body_length:
        fields.append((b"l", str(len(body)).encode()))
    fields += [
        (b"h", b":".join(h.encode() for h in headers)),
        (b"bh", base64.b64encode(digest.digest())),
        (b"b", b"0" * 60),
    ]
    value = signer.gen_header(
        fields, include, canon, b"DKIM-Signature", parse_pem_private_key(key.private_pem)
    )
    return b"DKIM-Signature: " + value + raw


# ---------------------------------------------------------------- approver messages


def _crlf(text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")


def plain_message(
    text: str,
    *,
    from_: str = f"Test Approver <{APPROVER}>",
    subject: str = "Re: Ke schválení",
    extra_headers: tuple[str, ...] = (),
) -> bytes:
    """A single-part text/plain message, as UTF-8 8bit."""
    head = [
        f"From: {from_}",
        f"To: {MAILBOX}",
        f"Subject: {subject}",
        "Date: Mon, 9 Nov 2026 10:00:00 +0100",
        "References: <request@example.org>",
        "In-Reply-To: <request@example.org>",
        "Message-Id: <reply@example.com>",
        "MIME-Version: 1.0",
        'Content-Type: text/plain; charset="utf-8"',
        "Content-Transfer-Encoding: 8bit",
        *extra_headers,
    ]
    return _crlf("\n".join(head) + "\n\n" + text)


def iphone_reply(
    first_line: str,
    *,
    from_: str = f"Test Approver <{APPROVER}>",
    quoted: str = "> Kód: QUOTED-CODE\n> | Dobrý den,",
) -> bytes:
    """multipart/alternative with quoted-printable text/plain and a quoted original."""
    text = f"{first_line}\n\nOdesláno z iPhonu\n\n> Dne 9. 11. 2026 v 9:00 napsal(a):\n{quoted}\n"
    plain = quopri.encodestring(_crlf(text)).decode("ascii")
    html = quopri.encodestring(_crlf(f"<html><body>{first_line}</body></html>")).decode("ascii")
    message = (
        f"From: {from_}\n"
        f"To: {MAILBOX}\n"
        "Subject: Re: Ke schválení\n"
        "Date: Mon, 9 Nov 2026 10:00:00 +0100\n"
        "References: <request@example.org>\n"
        "In-Reply-To: <request@example.org>\n"
        "Message-Id: <reply@example.com>\n"
        "Mime-Version: 1.0 (1.0)\n"
        'Content-Type: multipart/alternative; boundary="Apple-Mail-1"\n'
        "\n"
        "--Apple-Mail-1\n"
        'Content-Type: text/plain; charset="utf-8"\n'
        "Content-Transfer-Encoding: quoted-printable\n"
        "\n"
        f"{plain}\n"
        "--Apple-Mail-1\n"
        'Content-Type: text/html; charset="utf-8"\n'
        "Content-Transfer-Encoding: quoted-printable\n"
        "\n"
        f"{html}\n"
        "--Apple-Mail-1--\n"
    )
    return _crlf(message)


def html_only(first_line: str) -> bytes:
    return _crlf(
        f"From: Test Approver <{APPROVER}>\n"
        f"To: {MAILBOX}\n"
        "Subject: Re: Ke schválení\n"
        "Date: Mon, 9 Nov 2026 10:00:00 +0100\n"
        "References: <request@example.org>\n"
        "In-Reply-To: <request@example.org>\n"
        'Content-Type: text/html; charset="utf-8"\n'
        "Content-Transfer-Encoding: 8bit\n"
        "\n"
        f"<html><body>{first_line}</body></html>\n"
    )


# ---------------------------------------------------------------- fake IMAP


@dataclass
class StoredMessage:
    uid: int
    raw: bytes
    flags: tuple[str, ...]
    internaldate: float


@dataclass
class FakeImapServer:
    folders: dict[str, list[StoredMessage]] = field(default_factory=lambda: {"INBOX": [], "Drafts": []})
    next_uid: int = 1
    fetch_specs: list[str] = field(default_factory=list)
    selected: list[str] = field(default_factory=list)
    ssl_contexts: list = field(default_factory=list)

    def store(self, folder: str, raw: bytes, flags: tuple[str, ...], internaldate: float) -> StoredMessage:
        message = StoredMessage(self.next_uid, raw, flags, internaldate)
        self.next_uid += 1
        self.folders.setdefault(folder, []).append(message)
        return message


def imap_date(epoch: float) -> str:
    moment = time.gmtime(epoch)
    return (
        f"{moment.tm_mday:02d}-{MONTHS[moment.tm_mon - 1]}-{moment.tm_year} "
        f"{moment.tm_hour:02d}:{moment.tm_min:02d}:{moment.tm_sec:02d} +0000"
    )


class _FakeProtocol:
    """The part of imaplib.IMAP4 the server touches through MailBox.client."""

    def __init__(self, box: "FakeMailBox") -> None:
        self._box = box

    def uid(self, command: str, uid: str, spec: str):
        assert command == "FETCH"
        self._box.server.fetch_specs.append(spec)
        # BODY[] would set \Seen on a real server. Only the peeking form is acceptable.
        assert "BODY[]" not in spec, f"fetch must not mark mail as seen: {spec}"
        found = [m for m in self._box.messages() if str(m.uid) == str(uid)]
        if not found:
            return "OK", [None]
        message = found[0]
        flags = " ".join(message.flags)
        meta = (
            f'{message.uid} (UID {message.uid} FLAGS ({flags}) '
            f'INTERNALDATE "{imap_date(message.internaldate)}" RFC822.SIZE {len(message.raw)}'
        )
        if "BODY.PEEK[]" not in spec:
            return "OK", [f"{meta})".encode()]
        return "OK", [(f"{meta} BODY[] {{{len(message.raw)}}}".encode(), message.raw), b")"]


class FakeMailBox:
    """Stands in for imap_tools.MailBox. One instance per IMAP operation, like the real one."""

    server: FakeImapServer  # set on the subclass made by Harness

    def __init__(self, host: str = "", port: int = 993, ssl_context=None, **_: object) -> None:
        self.server.ssl_contexts.append(ssl_context)
        self.current = "INBOX"
        self.client = _FakeProtocol(self)
        self.folder = SimpleNamespace(
            list=lambda: [SimpleNamespace(name=name) for name in self.server.folders],
            status=lambda name, _items: {
                "MESSAGES": len(self.server.folders.get(name, [])),
                "UNSEEN": 0,
            },
            exists=lambda name: name in self.server.folders,
            create=lambda name: self.server.folders.setdefault(name, []),
        )

    def login(self, _user: str, _password: str, initial_folder: str = "INBOX") -> "FakeMailBox":
        self.current = initial_folder
        self.server.selected.append(initial_folder)
        return self

    def __enter__(self) -> "FakeMailBox":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def messages(self) -> list[StoredMessage]:
        return self.server.folders.setdefault(self.current, [])

    def uids(self, _criteria: object = "ALL", **_: object) -> list[str]:
        return [str(m.uid) for m in self.messages()]

    def fetch(self, criteria: object = "ALL", **_: object) -> list[MailMessage]:
        wanted = re.search(r"UID (\d+)", str(criteria))
        found = [m for m in self.messages() if not wanted or str(m.uid) == wanted.group(1)]
        return [MailMessage.from_bytes(m.raw) for m in found]

    def append(self, raw: bytes, folder: str, flag_set: list[str] | None = None, **_: object) -> None:
        self.server.store(folder, bytes(raw), tuple(flag_set or ()), time.time())


# ---------------------------------------------------------------- fake SMTP


@dataclass
class Outgoing:
    recipients: list[str]
    message: email.message.EmailMessage

    @property
    def text(self) -> str:
        parsed = email.message_from_bytes(self.message.as_bytes(), policy=policy.default)
        return parsed.get_content()

    @property
    def subject(self) -> str:
        parsed = email.message_from_bytes(self.message.as_bytes(), policy=policy.default)
        return parsed["Subject"]


class FakeSmtp:
    outbox: list[Outgoing]  # set on the subclass made by Harness
    failure: dict  # {"error": Exception | None}
    contexts: list

    def __init__(self, _host: str = "", _port: int = 0, timeout: int = 0, context=None) -> None:
        self.contexts.append(context)

    def starttls(self, context=None) -> None:
        self.contexts.append(context)

    def login(self, _user: str, _password: str) -> None:
        return None

    def send_message(self, message, to_addrs=None) -> None:
        if self.failure["error"] is not None:
            raise self.failure["error"]
        self.outbox.append(Outgoing(list(to_addrs), message))

    def quit(self) -> None:
        return None


# ---------------------------------------------------------------- harness


class Harness:
    """The registered MCP tools over fake IMAP/SMTP/DNS, with every result recorded."""

    def __init__(self, tmp_path: Path, monkeypatch, keys: dict[str, TestKey], **overrides: str) -> None:
        self.tmp_path = tmp_path
        self.keys = keys
        self.now = time.time()
        self.imap = FakeImapServer()
        self.outbox: list[Outgoing] = []
        self.smtp_failure: dict = {"error": None}
        self.smtp_contexts: list = []
        self.results: list[dict] = []
        self.dns: dict[str, bytes | None] = {
            "sel._domainkey.example.com.": keys["approver"].txt(),
            "sel._domainkey.evil.example.": keys["attacker"].txt(),
        }
        self.dns_error: Exception | None = None
        self.dns_queries: list[str] = []

        self.box_class = box_class = type("BoundMailBox", (FakeMailBox,), {"server": self.imap})
        smtp_class = type(
            "BoundSmtp",
            (FakeSmtp,),
            {"outbox": self.outbox, "failure": self.smtp_failure, "contexts": self.smtp_contexts},
        )
        monkeypatch.setattr("mail_mcp.imap.MailBox", box_class)
        monkeypatch.setattr("mail_mcp.smtp.smtplib.SMTP_SSL", smtp_class)
        monkeypatch.setattr("mail_mcp.smtp.smtplib.SMTP", smtp_class)

        self.settings = make_settings(tmp_path, **overrides)
        self.build()

    def build(self) -> None:
        """Register the tools. Calling it again is what a server restart does."""
        self.mcp = FastMCP("test")
        tools.register(self.mcp, self.settings, dnsfunc=self.dns_lookup, clock=lambda: self.now)

    def imap_box(self) -> FakeMailBox:
        return self.box_class()

    def dns_lookup(self, name, timeout: int = 5):
        name = name.decode() if isinstance(name, bytes) else name
        self.dns_queries.append(name)
        if self.dns_error is not None:
            raise self.dns_error
        return self.dns.get(name)

    async def tool_names(self) -> list[str]:
        async with Client(self.mcp) as client:
            return sorted(t.name for t in await client.list_tools())

    async def call(self, name: str, **arguments) -> dict:
        async with Client(self.mcp) as client:
            result = await client.call_tool(name, arguments)
        data = result.structured_content
        self.results.append(data)
        return data

    # ---- the approval flow, spelled out so attack tests read as the attack ----

    async def request(self, to: str = RECIPIENT, subject: str = SUBJECT, body: str = BODY) -> "Pending":
        result = await self.call("mail_request_approval", to=to, subject=subject, body=body)
        assert result["status"] == "approval_requested", result
        return Pending(result["approval_id"], self.code_from_last_request(), to, subject, body)

    def code_from_last_request(self) -> str:
        requests = self.to_approver()
        assert requests, "no approval request reached the approver"
        match = re.search(r"^souhlasím (\S+)$", requests[-1].text, re.MULTILINE)
        assert match, requests[-1].text
        return match.group(1)

    async def send(self, pending: "Pending", **changes) -> dict:
        arguments = {
            "approval_id": pending.approval_id,
            "to": pending.to,
            "subject": pending.subject,
            "body": pending.body,
            **changes,
        }
        return await self.call("mail_send_approved", **arguments)

    def deliver(self, raw: bytes, *, flags: tuple[str, ...] = (), folder: str = "INBOX", internaldate: float | None = None):
        return self.imap.store(folder, raw, flags, self.now if internaldate is None else internaldate)

    def approve(self, pending: "Pending", first_line: str | None = None) -> None:
        """A genuine, correctly signed reply from the approver lands in INBOX."""
        line = first_line if first_line is not None else f"souhlasím {pending.code}"
        self.deliver(dkim_sign(iphone_reply(line), self.keys["approver"]))

    # ---- what happened ----

    def to_approver(self) -> list[Outgoing]:
        return [m for m in self.outbox if APPROVER in m.recipients]

    def delivered(self) -> list[Outgoing]:
        """Mail that left for anyone but the approver."""
        return [m for m in self.outbox if APPROVER not in m.recipients]

    def log(self) -> list[dict]:
        path = self.settings.data_dir / "sent_log.json"
        return json.loads(path.read_text())["sent"] if path.exists() else []

    def log_kinds(self) -> list[str]:
        return [entry["kind"] for entry in self.log()]


@dataclass(frozen=True)
class Pending:
    approval_id: str
    code: str
    to: str
    subject: str
    body: str


def make_settings(data_dir: Path, **overrides: str) -> Settings:
    values = {
        "MAILBOX_LABEL": MAILBOX,
        "BASE_URL": "https://mcp-mail.example.org",
        "GITHUB_CLIENT_ID": "id",
        "GITHUB_CLIENT_SECRET": "secret",
        "ALLOWED_GITHUB_LOGINS": "tester",
        "IMAP_HOST": "imap.example.org",
        "IMAP_USER": MAILBOX,
        "IMAP_PASS": "imap-password",
        "SMTP_HOST": "smtp.example.org",
        "SMTP_USER": MAILBOX,
        "SMTP_PASS": "smtp-password",
        "ALLOWED_RECIPIENTS": ALLOWLISTED,
        "DATA_DIR": str(data_dir),
        **overrides,
    }
    return Settings(_env_file=None, **values)
