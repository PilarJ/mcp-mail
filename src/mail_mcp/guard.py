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

# sent_log entries that record no mail going to `to`, so the repeat guard ignores them.
APPROVAL_EVENT_KINDS = frozenset({"approval_requested", "approved", "approval_voided"})


def extract_address(value: str) -> str:
    """Pull the bare address out of `Name <addr@example.com>`."""
    match = _ADDR_RE.search(value or "")
    return match.group(0).lower() if match else (value or "").strip().lower()


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
        rejected = [
            addr for addr in (extract_address(r) for r in recipients)
            if addr and not self._permits(addr)
        ]
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
