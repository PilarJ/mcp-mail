"""Per-message human approval for sending outside the allowlist.

The model reads mail written by strangers, so nothing it reads or writes may be able to
create an approval. The server decides by itself, from the raw message in IMAP: right
sender, valid DKIM signature for the approver's own domain, right code as the first line
of the new text, and content byte-identical to what the approver was shown.

Every check fails closed. Refusal reasons go back to the model, so they are fixed strings:
never the code, never the approver's address, never anything taken from a message.
"""

from __future__ import annotations

import datetime
import email
import hashlib
import hmac
import logging
import os
import quopri
import re
import secrets
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from email import policy
from pathlib import Path
from typing import Callable, Iterator

import anyio
import dkim
from dkim.dnsplug import get_txt
from dkim.util import parse_tag_value

from . import smtp
from .guard import ADDRESS_RE, SentLog
from .imap import ImapClient, MailError, RawMessage
from .settings import Settings

logger = logging.getLogger(__name__)

# Easy to type on a phone: no 0/O, 1/I/L. 31 symbols, 10 of them: about 49 bits.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 10
_PBKDF2_ROUNDS = 200_000

MAX_SUBJECT_CHARS = 998
# Newest messages looked at per send, and the largest one worth verifying. A flood of
# forged mail can hide a real approval this way, but it cannot create one.
MAX_CANDIDATES = 50
MAX_MESSAGE_BYTES = 2_000_000
# The clock of the IMAP server may run behind ours.
INTERNALDATE_SKEW_SECONDS = 300

# RFC 5322 mailbox, the strict subset. No comments, no groups, no lists. An atom cannot hold
# "@", so an address in a display name must be quoted, and then it is only a display name.
_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+"
_QUOTED = r'"(?:[^"\\]|\\.)*"'
_WORD = rf"(?:{_ATOM}|{_QUOTED})"
_MAILBOX_RE = re.compile(
    rf"(?:{_WORD}(?:[ \t]+{_WORD})*[ \t]*)?<(?P<angle>[^<>\s]+)>"
    r"|(?P<bare>[^<>\s,;:()\"]+@[^<>\s,;:()\"]+)"
)
_MAX_FROM_CHARS = 1000

_HEADER_RE = re.compile(rb"([\x21-\x39\x3b-\x7e]+):(.*)", re.DOTALL)
_HEADER_CONTROL_RE = re.compile(rb"[\x00-\x08\x0a-\x1f\x7f]")

# One of each, or a parser has to choose. Which one it chooses is not the same everywhere.
_MIME_SINGLETONS = frozenset(
    {b"mime-version", b"content-type", b"content-transfer-encoding", b"content-disposition"}
)
_READABLE_MULTIPARTS = frozenset({"multipart/alternative", "multipart/mixed"})
_NO_ENCODING = frozenset({"", "7bit", "8bit", "binary"})

# Characters that could make the text the approver sees differ from what it says.
_BIDI_CONTROLS = frozenset("؜‎‏‪‫‬‭‮⁦⁧⁨⁩")

_APPROVAL_WORDS = frozenset({"souhlasím", "souhlasim"})

DnsFunc = Callable[..., object]


class ApprovalConfigError(ValueError):
    """The configuration must not start: approval would be open or could never work."""


class ApprovalStateError(RuntimeError):
    """The state file cannot be trusted."""


class ApprovalLimitReached(RuntimeError):
    pass


# ---------------------------------------------------------------- configuration


def check_config(settings: Settings) -> None:
    approver = settings.approver_address
    if not approver:
        return
    if not ADDRESS_RE.fullmatch(approver):
        raise ApprovalConfigError("APPROVER_ADDRESS must be one bare ASCII email address.")
    if approver.lower() in _own_addresses(settings):
        raise ApprovalConfigError(
            "APPROVER_ADDRESS must not be this mailbox's own address: the model can write "
            "to its own mailbox."
        )
    if settings.approval_ttl_hours <= 0:
        raise ApprovalConfigError("APPROVAL_TTL_HOURS must be positive.")
    if settings.approval_max_per_hour <= 0 or settings.approval_max_pending <= 0:
        raise ApprovalConfigError("APPROVAL_MAX_PER_HOUR and APPROVAL_MAX_PENDING must be positive.")


def _own_addresses(settings: Settings) -> set[str]:
    return {settings.smtp_user.lower(), settings.imap_user.lower(), settings.mailbox_label.lower()}


# ---------------------------------------------------------------- concept and code


def fingerprint(to: str, subject: str, body: str) -> str:
    """SHA-256 with each field length-prefixed, so text cannot move between fields unseen.

    No normalisation of any kind: what is compared is what would be sent.
    """
    digest = hashlib.sha256(b"mcp-mail-approval-v1")
    for value in (to, subject, body):
        data = value.encode("utf-8", "surrogatepass")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _has_unsafe_character(text: str, allowed: str) -> bool:
    for char in text:
        if char in allowed:
            continue
        if unicodedata.category(char) in ("Cc", "Zl", "Zp", "Cs") or char in _BIDI_CONTROLS:
            return True
    return False


def concept_problem(settings: Settings, to: str, subject: str, body: str) -> str | None:
    if not ADDRESS_RE.fullmatch(to):
        return "The recipient must be exactly one bare ASCII address, with no display name."
    if to.lower() in _own_addresses(settings):
        return "The recipient must not be this mailbox's own address."
    if len(subject) > MAX_SUBJECT_CHARS:
        return f"The subject is over the {MAX_SUBJECT_CHARS} character limit."
    if len(body) > settings.max_body_chars:
        return f"The body is over the {settings.max_body_chars} character limit."
    if _has_unsafe_character(subject, allowed=""):
        return "The subject must not contain control characters or line breaks."
    # Anything a mail client may render as a line break would escape the per-line prefix
    # in the approval message. Line breaks in the body are a plain "\n".
    if _has_unsafe_character(body, allowed="\n\t"):
        return (
            "The body must not contain control characters, bidirectional controls or line "
            'breaks other than "\\n".'
        )
    return None


def new_code() -> str:
    symbols = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    middle = CODE_LENGTH // 2
    return f"{symbols[:middle]}-{symbols[middle:]}"


def _normalise_code(token: str) -> str | None:
    if not token.isascii():
        return None
    symbols = token.upper().replace("-", "", 1)
    if len(symbols) != CODE_LENGTH or not set(symbols) <= set(CODE_ALPHABET):
        return None
    return symbols


def _hash_code(symbols: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", symbols.encode("ascii"), salt, _PBKDF2_ROUNDS)


def approval_text(to: str, subject: str, body: str, code: str, expires_at: float) -> str:
    expires = datetime.datetime.fromtimestamp(expires_at, datetime.timezone.utc)
    lines = [
        f"Příjemce: {to}",
        f"Předmět: {subject}",
        f"Kód: {code}",
        "",
        "Schválíte odpovědí nebo přeposláním, ve kterých je první řádek přesně:",
        f"souhlasím {code}",
        "",
        f"Platí do {expires:%Y-%m-%d %H:%M} UTC. Jakákoli změna příjemce, předmětu nebo textu "
        "schválení zruší.",
        "",
        'Text zprávy, každý řádek začíná znaky "| ":',
    ]
    lines += [f"| {line}" for line in body.split("\n")]
    lines += ["", f"Příjemce (znovu): {to}"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- what the approver sent


def _header_fields(raw: bytes) -> list[tuple[bytes, bytes]] | None:
    """Parse the header section strictly. Anything odd returns None, and None refuses."""
    fields: list[list[bytes]] = []
    for line in re.split(rb"\r?\n", raw):
        if not line:
            return [(name, value) for name, value in fields]
        if _HEADER_CONTROL_RE.search(line):
            return None
        if line[0] in b" \t":
            if not fields:
                return None
            fields[-1][1] += line
            continue
        match = _HEADER_RE.fullmatch(line)
        if match is None:
            return None
        fields.append([match.group(1), match.group(2)])
    return None


def _sender_problem(fields: list[tuple[bytes, bytes]], approver: str) -> str | None:
    values = [value for name, value in fields if name.lower() == b"from"]
    if len(values) != 1:
        return "the message must have exactly one From header"
    value = values[0]
    if not value.isascii() or len(value) > _MAX_FROM_CHARS:
        return "the From header is not plain ASCII"
    match = _MAILBOX_RE.fullmatch(value.decode("ascii").strip())
    if match is None:
        return "the From header is not exactly one mailbox"
    if (match.group("angle") or match.group("bare")) != approver:
        return "the From address is not the approver"
    return None


def _has_stray_line_ending(raw: bytes) -> bool:
    """A lone CR or LF. dkimpy and the email parser do not agree on where such a line ends."""
    crlf = raw.count(b"\r\n")
    return raw.count(b"\r") != crlf or raw.count(b"\n") != crlf


def _declared_mime(fields: list[tuple[bytes, bytes]]) -> email.message.EmailMessage | None:
    """The MIME headers on their own, or None if any repeats or is malformed."""
    names = [name.lower() for name, _ in fields]
    if any(names.count(name) > 1 for name in _MIME_SINGLETONS):
        return None
    section = b"".join(
        name + b":" + value + b"\r\n" for name, value in fields if name.lower() in _MIME_SINGLETONS
    )
    declared = email.message_from_bytes(section + b"\r\n", policy=policy.default)
    for name in ("content-type", "content-transfer-encoding"):
        if name in declared and declared[name].defects:
            return None
    return declared


def _transfer_encoding(declared: email.message.EmailMessage) -> str:
    return str(declared.get("content-transfer-encoding", "")).strip().lower()


def _single_part_line(declared: email.message.EmailMessage, line: bytes) -> str | None:
    """The line as written. Base64 would need an unsigned header to give it its meaning."""
    if _transfer_encoding(declared) not in _NO_ENCODING | {"quoted-printable"}:
        return None
    # A trailing "=" means the line is not all here: in quoted-printable it is a soft break and
    # the text goes on below, in 8bit it is a character of the text. Wrapped first lines are
    # not supported, whatever the unsigned header claims, so neither reading can be cut short.
    if line.rstrip(b" \t").endswith(b"="):
        return None
    # Always UTF-8 and always this one decoding, whatever the unsigned charset and encoding
    # say. A line that reads as the phrase this way was written as the phrase.
    text = quopri.decodestring(line).decode("utf-8")
    # One signed line stays one line: a decoded break must not make a later "first line".
    return text if len(text.splitlines()) == 1 else None


def _first_part_text(declared: email.message.EmailMessage, lines: list[bytes], start: int) -> str | None:
    """The first part, which the signed body itself must show to be the first part."""
    boundary = declared.get_boundary()
    if (
        declared.get_content_type() not in _READABLE_MULTIPARTS
        or _transfer_encoding(declared) not in _NO_ENCODING
        or not boundary
    ):
        return None
    # The body must open with this delimiter. No preamble: a preamble would let the unsigned
    # boundary pick any look-alike part out of a forwarded text.
    delimiter = b"--" + boundary.encode("ascii")
    if lines[start].rstrip(b" \t") != delimiter:
        return None
    for end in range(start + 1, len(lines)):
        if lines[end].startswith(delimiter):
            if lines[end].rstrip(b" \t") not in (delimiter, delimiter + b"--"):
                return None
            break
    else:
        return None

    # The headers of this part are inside the signature, so they can be believed.
    octets = b"\r\n".join(lines[start + 1 : end]) + b"\r\n"
    fields = _header_fields(octets)
    if fields is None or _declared_mime(fields) is None:
        return None
    part = email.message_from_bytes(octets, policy=policy.default)
    if part.get_content_type() != "text/plain" or part.get_content_disposition() == "attachment":
        return None
    text = part.get_content()
    return None if part.defects else text


def _plain_text(raw: bytes, fields: list[tuple[bytes, bytes]]) -> str | None:
    """The approver's text, read from the octets the signature covers.

    Real approver mail signs no MIME header, so Content-Type and Content-Transfer-Encoding
    can be added or changed and the signature still verifies. They may therefore never give
    the signed body a meaning it does not already have. A single part is read as written.
    A multipart body must itself open with the delimiter, and its first part carries its own
    signed headers. Anything else refuses, and that includes real mail in rarer shapes.
    """
    try:
        declared = _declared_mime(fields)
        if declared is None:
            return None
        lines = dkim.rfc822_parse(raw)[1].split(b"\r\n")
        start = next((index for index, line in enumerate(lines) if line.strip()), None)
        if start is None:
            return None
        if declared.get_content_maintype() == "multipart":
            return _first_part_text(declared, lines, start)
        if declared.get_content_type() != "text/plain":
            return None
        return _single_part_line(declared, lines[start])
    except (LookupError, ValueError, TypeError, AttributeError, dkim.DKIMException):
        return None


def approval_code_in(text: str) -> str | None:
    """The code, if the first non-empty line is exactly the approval phrase.

    Quoted text is not skipped on purpose. A reply that starts with a quote approves
    nothing, whatever follows, because a concept cannot then steer what counts as new text.
    """
    for line in text.splitlines():
        if line.strip():
            tokens = unicodedata.normalize("NFC", line).split()
            if len(tokens) == 2 and tokens[0].lower() in _APPROVAL_WORDS:
                return _normalise_code(tokens[1])
            return None
    return None


def _testing_key(record: object) -> bool:
    data = record.encode("ascii") if isinstance(record, str) else record
    flags = parse_tag_value(data).get(b"t", b"")
    return b"y" in {flag.strip().lower() for flag in flags.split(b":")}


def _signature_problem(
    verifier: dkim.DKIM, index: int, value: bytes, domain: str, dnsfunc: DnsFunc, clock: Callable[[], float]
) -> str | None:
    tags = parse_tag_value(value)
    if tags[b"d"].decode("ascii").lower() != domain.lower():
        return "DKIM: signed by another domain"
    if tags[b"a"] != b"rsa-sha256":
        return "DKIM: only rsa-sha256 signatures are accepted"
    if b"l" in tags:
        return "DKIM: a signature with an l= tag covers only part of the body"
    if b"from" not in {name.strip().lower() for name in tags[b"h"].split(b":")}:
        return "DKIM: the signature does not cover From"

    # dkimpy allows 10 hours past x=, which is not the same thing as not expired.
    def expired() -> bool:
        return b"x" in tags and int(tags[b"x"]) <= clock()

    if expired():
        return "DKIM: the signature has expired"

    lookup: dict[str, object] = {}

    def recording_lookup(name, timeout: int = 5):
        try:
            lookup["record"] = dnsfunc(name, timeout=timeout)
        except Exception:
            lookup["failed"] = True
            raise
        return lookup["record"]

    try:
        valid = verifier.verify(idx=index, dnsfunc=recording_lookup)
    except dkim.DKIMException as exc:
        logger.info("DKIM verification error: %s", type(exc).__name__)
        return "DKIM: verification failed, the message may have been altered"
    except Exception as exc:
        if lookup.get("failed"):
            logger.warning("DKIM key lookup failed: %s", type(exc).__name__)
            return "DKIM: the DNS lookup for the key failed"
        raise
    if lookup.get("failed") or "record" not in lookup:
        return "DKIM: the DNS lookup for the key failed"
    if not lookup["record"]:
        return "DKIM: no key for the signature was found in DNS"
    if not valid:
        return "DKIM: the signature does not verify"
    if _testing_key(lookup["record"]):
        return "DKIM: the DNS key is marked as testing (t=y)"
    # Again, on a clock read after the DNS lookup.
    if expired():
        return "DKIM: the signature has expired"
    return None


def dkim_problem(raw: bytes, domain: str, dnsfunc: DnsFunc, clock: Callable[[], float]) -> str | None:
    """None if at least one signature passes. Verifies the raw bytes as fetched."""
    try:
        verifier = dkim.DKIM(raw, logger=logger)
    except Exception:
        return "DKIM: the message cannot be parsed"
    values = [value for name, value in verifier.headers if name.lower() == b"dkim-signature"]
    if not values:
        return "DKIM: the message has no signature"
    problems: list[str] = []
    for index, value in enumerate(values):
        try:
            problem = _signature_problem(verifier, index, value, domain, dnsfunc, clock)
        except Exception as exc:
            logger.info("DKIM signature refused: %s", type(exc).__name__)
            problem = "DKIM: the signature is malformed"
        if problem is None:
            return None
        problems.append(problem)
    return "; ".join(dict.fromkeys(problems))


# ---------------------------------------------------------------- state


@dataclass(frozen=True)
class Approval:
    approval_id: str
    fingerprint: str
    code_salt: bytes
    code_hash: bytes
    state: str
    created_at: float
    expires_at: float


_STATES = frozenset({"pending", "used", "voided", "failed"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    code_salt BLOB NOT NULL,
    code_hash BLOB NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'used', 'voided', 'failed')),
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
)
"""


class ApprovalStore:
    """SQLite in DATA_DIR. Any error at all surfaces as ApprovalStateError, which refuses."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            os.close(os.open(self._path, os.O_CREAT | os.O_WRONLY, 0o600))
            connection = sqlite3.connect(self._path, timeout=10, isolation_level=None)
        except (OSError, sqlite3.Error) as exc:
            raise ApprovalStateError(str(exc)) from exc
        try:
            connection.execute(_SCHEMA)
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ApprovalStateError("integrity check failed")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except sqlite3.Error as exc:
            raise ApprovalStateError(str(exc)) from exc
        finally:
            connection.close()  # without a COMMIT, this rolls back

    def create(self, approval: Approval, max_per_hour: int, max_pending: int, now: float) -> None:
        with self._connection() as db:
            recent = db.execute(
                "SELECT COUNT(*) FROM approvals WHERE created_at > ?", (now - 3600,)
            ).fetchone()[0]
            if recent >= max_per_hour:
                raise ApprovalLimitReached(f"The limit of {max_per_hour} approval requests per hour is reached.")
            pending = db.execute(
                "SELECT COUNT(*) FROM approvals WHERE state = 'pending' AND expires_at > ?", (now,)
            ).fetchone()[0]
            if pending >= max_pending:
                raise ApprovalLimitReached(f"The limit of {max_pending} pending approvals is reached.")
            db.execute(
                "INSERT INTO approvals (id, fingerprint, code_salt, code_hash, state, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (
                    approval.approval_id,
                    approval.fingerprint,
                    approval.code_salt,
                    approval.code_hash,
                    approval.created_at,
                    approval.expires_at,
                ),
            )

    def get(self, approval_id: str) -> Approval | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT id, fingerprint, code_salt, code_hash, state, created_at, expires_at "
                "FROM approvals WHERE id = ?",
                (approval_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            approval = Approval(row[0], str(row[1]), bytes(row[2]), bytes(row[3]), str(row[4]), float(row[5]), float(row[6]))
        except (TypeError, ValueError) as exc:
            raise ApprovalStateError("malformed row") from exc
        if approval.state not in _STATES:
            raise ApprovalStateError("unknown approval state")
        return approval

    def _move(self, approval_id: str, to_state: str, clock: Callable[[], float] | None = None) -> bool:
        """pending -> to_state. True for exactly one caller, however many race for it."""
        query = "UPDATE approvals SET state = ? WHERE id = ? AND state = 'pending'"
        parameters: tuple = (to_state, approval_id)
        with self._connection() as db:
            if clock is not None:
                # Read inside the transaction, once this caller holds the lock. Verification
                # before it can take long enough for the approval to expire.
                query += " AND expires_at > ?"
                parameters += (clock(),)
            return db.execute(query, parameters).rowcount == 1

    def consume(self, approval_id: str, clock: Callable[[], float]) -> bool:
        return self._move(approval_id, "used", clock)

    def void(self, approval_id: str) -> bool:
        return self._move(approval_id, "voided")

    def mark_failed(self, approval_id: str) -> bool:
        return self._move(approval_id, "failed")


# ---------------------------------------------------------------- the service


class ApprovalService:
    def __init__(
        self,
        settings: Settings,
        imap: ImapClient,
        sent_log: SentLog,
        *,
        dnsfunc: DnsFunc | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._s = settings
        self._imap = imap
        self._log = sent_log
        self._dns = dnsfunc or get_txt
        self._clock = clock
        self._store = ApprovalStore(settings.approval_db_path)
        self._approver = settings.approver_address
        self._approver_domain = self._approver.rpartition("@")[2]

    def _refused(self, detail: str) -> dict:
        return {"status": "refused", "mailbox": self._s.mailbox_label, "detail": detail}

    async def request(self, to: str, subject: str, body: str) -> dict:
        try:
            return await self._request(to, subject, body)
        except ApprovalStateError as exc:
            logger.error("Approval state unusable: %s", type(exc.__cause__).__name__)
            return self._refused("The approval state cannot be read, so approval is unavailable.")

    async def _request(self, to: str, subject: str, body: str) -> dict:
        problem = concept_problem(self._s, to, subject, body)
        if problem:
            return self._refused(problem)

        now = self._clock()
        code = new_code()
        salt = secrets.token_bytes(16)
        code_hash = await anyio.to_thread.run_sync(_hash_code, _normalise_code(code), salt)
        approval = Approval(
            approval_id=secrets.token_hex(8),
            fingerprint=fingerprint(to, subject, body),
            code_salt=salt,
            code_hash=code_hash,
            state="pending",
            created_at=now,
            expires_at=now + self._s.approval_ttl_hours * 3600,
        )
        try:
            await anyio.to_thread.run_sync(
                self._store.create, approval, self._s.approval_max_per_hour, self._s.approval_max_pending, now
            )
        except ApprovalLimitReached as exc:
            return self._refused(str(exc))

        message = smtp.build_message(
            self._s,
            [self._approver],
            f"Ke schválení: zpráva {approval.approval_id}",
            approval_text(to, subject, body, code, approval.expires_at),
        )
        try:
            await smtp.send(self._s, message, [self._approver])
        except smtp.SendError as exc:
            # Not str(exc): an SMTP error can name the approver.
            logger.warning("Approval request not sent: %s", type(exc.__cause__).__name__)
            await anyio.to_thread.run_sync(self._store.mark_failed, approval.approval_id)
            return self._refused("The approval request could not be sent. Nothing is pending; try again later.")

        self._log.record([to], subject, "approval_requested")
        return {
            "status": "approval_requested",
            "mailbox": self._s.mailbox_label,
            "approval_id": approval.approval_id,
            "to": to,
            "subject": subject,
            "expires_at": datetime.datetime.fromtimestamp(approval.expires_at, datetime.timezone.utc).isoformat(),
            "detail": (
                "The approver has been asked by email. Call mail_send_approved with this "
                "approval_id and exactly the same to, subject and body once they have replied. "
                "Any difference voids the approval for good."
            ),
        }

    async def send(self, approval_id: str, to: str, subject: str, body: str) -> dict:
        try:
            return await self._send(approval_id, to, subject, body)
        except ApprovalStateError as exc:
            logger.error("Approval state unusable: %s", type(exc.__cause__).__name__)
            return self._refused("The approval state cannot be read, so nothing was sent.")

    async def _send(self, approval_id: str, to: str, subject: str, body: str) -> dict:
        approval = await anyio.to_thread.run_sync(self._store.get, approval_id)
        if approval is None:
            return self._refused("There is no such approval.")
        if approval.state != "pending":
            return self._refused(
                f"This approval is {approval.state} and cannot be used again. Request a new one."
            )
        if self._clock() >= approval.expires_at:
            return self._refused("This approval has expired. Request a new one.")

        if not hmac.compare_digest(approval.fingerprint, fingerprint(to, subject, body)):
            if await anyio.to_thread.run_sync(self._store.void, approval_id):
                self._log.record([to], subject[:MAX_SUBJECT_CHARS], "approval_voided")
            return self._refused(
                "This differs from what was submitted for approval, so the approval is now void "
                "for good. Sending the original will not work either. Request a new one."
            )

        problems = await self._approval_problems(approval)
        if problems:
            return self._refused(problems)

        when = self._log.recently_sent_to([to])
        if when:
            return self._refused(
                f"This mailbox already wrote to this recipient at {when}, within the "
                f"{self._s.dedup_minutes} minute repeat guard. The approval stays valid."
            )

        # Spent before SMTP: two concurrent calls send once, and a failure cannot be retried
        # into a second copy.
        if not await anyio.to_thread.run_sync(self._store.consume, approval_id, self._clock):
            current = await anyio.to_thread.run_sync(self._store.get, approval_id)
            if current is not None and current.state == "pending":
                return self._refused("This approval has expired. Request a new one.")
            return self._refused("This approval has already been used. Request a new one.")
        self._log.record([to], subject, "approved")

        message = smtp.build_message(self._s, [to], subject, body)
        try:
            await smtp.send(self._s, message, [to])
        except smtp.SendError as exc:
            logger.warning("Approved message not sent: %s", type(exc.__cause__).__name__)
            return self._refused("Sending failed. The approval stays spent, so request a new one.")

        self._log.record([to], subject, "sent_approved")
        return {"status": "sent", "mailbox": self._s.mailbox_label, "to": to, "subject": subject}

    async def _approval_problems(self, approval: Approval) -> str | None:
        """None once a message in INBOX proves the approval, otherwise why not."""
        since = datetime.datetime.fromtimestamp(approval.created_at - 86400, datetime.timezone.utc).date()
        try:
            candidates = await self._imap.approval_candidates(
                self._approver, since, MAX_CANDIDATES, MAX_MESSAGE_BYTES
            )
        except MailError as exc:
            # Not str(exc): the failed IMAP command contains the approver's address.
            logger.warning("Approval search failed: %s", type(exc.__cause__).__name__)
            return "The mailbox could not be searched for the approval, so nothing was sent."

        if not candidates:
            return "No approval has arrived yet."

        def verify() -> str | None:
            problems: list[str] = []
            for candidate in candidates:
                try:
                    problem = self._message_problem(candidate, approval)
                except Exception as exc:
                    logger.warning("Approval check failed: %s", type(exc).__name__)
                    problem = "the message could not be verified"
                if problem is None:
                    return None
                problems.append(problem)
            return "No approval is valid yet. Messages refused: " + "; ".join(dict.fromkeys(problems))

        return await anyio.to_thread.run_sync(verify)

    def _message_problem(self, message: RawMessage, approval: Approval) -> str | None:
        """Cheapest first, DKIM with its DNS lookup last."""
        if any(flag.lower() == "\\draft" for flag in message.flags):
            return "a draft is never an approval"
        if message.raw is None:
            return "the message is too large to verify"
        if _has_stray_line_ending(message.raw):
            return "the message has a stray line ending, so its parts cannot be told apart"
        fields = _header_fields(message.raw)
        if fields is None:
            return "the From header could not be parsed strictly"
        problem = _sender_problem(fields, self._approver)
        if problem:
            return problem
        if message.internaldate is None:
            return "the message has no usable INTERNALDATE"
        if message.internaldate > approval.expires_at:
            return "the message arrived after the approval expired"
        if message.internaldate < approval.created_at - INTERNALDATE_SKEW_SECONDS:
            return "the message is older than the approval request"

        text = _plain_text(message.raw, fields)
        if text is None:
            return "the message has no text/plain part that its signed body shows one way"
        symbols = approval_code_in(text)
        if symbols is None:
            return "its first line is not the approval phrase with a code"
        if not hmac.compare_digest(_hash_code(symbols, approval.code_salt), approval.code_hash):
            return "its code does not belong to this approval"

        return dkim_problem(message.raw, self._approver_domain, self._dns, self._clock)
