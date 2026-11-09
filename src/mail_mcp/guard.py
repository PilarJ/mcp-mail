"""Where mail is allowed to go, and a record of where it went.

This is the one control in the server that an email cannot argue with. A
`confirm=true` flag is decided by the model, which is reading text written by
whoever sent the message; the allowlist is decided by configuration on the
host. Anything that can be talked out of by a cleverly worded email is not a
security boundary, so the allowlist is the boundary.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

_ADDR_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# The only form of address that reaches SMTP: plain ASCII, one mailbox. No "%", which some
# servers still treat as routing to another host.
ADDRESS_RE = re.compile(r"[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# A display name holds no address and nothing that could end it early, quoted or not.
_DISPLAY_NAME_RE = re.compile(
    r'[^"<>@,;:()\[\]\\\x00-\x1f\x7f]*'
    r'|"[^"<>@\\\x00-\x1f\x7f]*"'
)
RECIPIENT_FORM = (
    "Each recipient must be exactly one address in plain ASCII, as addr@example.com or "
    "Name <addr@example.com>, with no other address in it."
)

# sent_log entries that record no mail going to `to`, so the repeat guard ignores them.
APPROVAL_EVENT_KINDS = frozenset({"approval_requested", "approved", "approval_voided"})


def extract_address(value: str) -> str:
    """Pull the bare address out of `Name <addr@example.com>`.

    For configuration and log entries only. It takes the first text shaped like an address,
    which need not be the one SMTP would use, so it must not decide where mail goes.
    """
    match = _ADDR_RE.search(value or "")
    return match.group(0).lower() if match else (value or "").strip().lower()


def parse_recipient(value: str) -> str | None:
    """The one mailbox in `addr@example.com` or `Name <addr@example.com>`, lower-cased.

    None for anything else. What this returns is what policy, the SMTP envelope, the message
    headers and the audit log all use, so no two of them can read a recipient differently.
    """
    text = value.strip(" ")
    name, bracket, rest = text.partition("<")
    if not bracket:
        address = text
    elif rest.endswith(">") and _DISPLAY_NAME_RE.fullmatch(name.strip(" ")):
        address = rest[:-1]
    else:
        return None
    return address.lower() if ADDRESS_RE.fullmatch(address) else None


def parse_recipients(values: list[str]) -> list[str] | None:
    """All of them as parse_recipient reads them, or None if any is not exactly one mailbox."""
    addresses = [parse_recipient(value) for value in values]
    return None if None in addresses else addresses


@dataclass
class GuardResult:
    allowed: bool
    rejected: list[str]
    reason: str = ""


class RecipientGuard:
    def __init__(self, allowed: list[str]) -> None:
        self._exact: set[str] = set()
        self._domains: set[str] = set()
        # "*" lifts the boundary for this mailbox. Only for an instance whose
        # owner accepts that an email it reads could talk it into sending.
        self._anyone = False
        for entry in allowed:
            entry = entry.strip().lower()
            if not entry:
                continue
            if entry == "*":
                self._anyone = True
            elif entry.startswith("@"):
                self._domains.add(entry[1:])
            else:
                self._exact.add(extract_address(entry))

    @property
    def configured(self) -> bool:
        return bool(self._anyone or self._exact or self._domains)

    def describe(self) -> dict:
        return {
            "addresses": sorted(self._exact),
            "domains": sorted(f"@{d}" for d in self._domains),
            "any_recipient": self._anyone,
            "sending_enabled": self.configured,
        }

    def _permits(self, address: str) -> bool:
        if self._anyone or address in self._exact:
            return True
        _, _, domain = address.partition("@")
        return domain in self._domains

    def check(self, recipients: list[str]) -> GuardResult:
        if not self.configured:
            return GuardResult(
                False,
                [],
                "Sending is disabled: ALLOWED_RECIPIENTS is empty. Add the "
                "permitted addresses to the server configuration — this cannot "
                "be overridden from a conversation.",
            )
        addresses = parse_recipients(recipients)
        if addresses is None:
            return GuardResult(False, [], f"{RECIPIENT_FORM} Sending was refused.")
        rejected = [addr for addr in addresses if not self._permits(addr)]
        if rejected:
            return GuardResult(
                False,
                rejected,
                "These recipients are not on this mailbox's allowlist: "
                + ", ".join(rejected)
                + ". Sending was refused. Do not attempt to route around this "
                "by choosing a different address.",
            )
        return GuardResult(True, [])


class SentLog:
    """Append-only record of what this server sent, for audit and dedup."""

    def __init__(self, path: Path, dedup_minutes: int = 0) -> None:
        self._path = path
        self._dedup_minutes = dedup_minutes

    def _read(self) -> list[dict]:
        try:
            return json.loads(self._path.read_text()).get("sent", [])
        except (OSError, ValueError):
            return []

    def recently_sent_to(self, recipients: list[str]) -> str | None:
        if self._dedup_minutes <= 0:
            return None
        cutoff = (datetime.now() - timedelta(minutes=self._dedup_minutes)).isoformat()
        targets = {extract_address(r) for r in recipients}
        for entry in self._read():
            if entry.get("sent_at", "") < cutoff or entry.get("kind") in APPROVAL_EVENT_KINDS:
                continue
            if extract_address(entry.get("to", "")) in targets:
                return entry.get("sent_at", "")
        return None

    def record(self, recipients: list[str], subject: str, kind: str) -> None:
        entries = self._read()
        now = datetime.now().isoformat(timespec="seconds")
        for recipient in recipients:
            entries.append(
                {
                    "to": extract_address(recipient),
                    "subject": subject,
                    "sent_at": now,
                    "kind": kind,
                }
            )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"sent": entries[-500:]}, indent=2, ensure_ascii=False))
        os.replace(tmp, self._path)

    def tail(self, limit: int = 20) -> list[dict]:
        return self._read()[-limit:]
