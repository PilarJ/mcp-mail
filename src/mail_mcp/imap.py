"""IMAP access.

`imap-tools` is synchronous and IMAP connections go stale when idle, so every
operation opens its own short-lived connection inside a worker thread. That
costs a login per call and buys not having to nurse a long-lived socket back
to life mid-request.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import anyio
from imap_tools import AND, MailBox, MailMessageFlags

from .settings import Settings
from .tls import client_context

logger = logging.getLogger(__name__)


class MailError(RuntimeError):
    pass


@dataclass(frozen=True)
class RawMessage:
    """A message exactly as IMAP returned it. raw is None if it is over the size limit."""

    uid: str
    raw: bytes | None
    flags: frozenset[str]
    internaldate: float | None


def parse_fetch_response(data: list) -> tuple[bytes, bytes | None]:
    """Split an imaplib FETCH response into its metadata and the one literal.

    Metadata can arrive before or after the literal. It is never searched for in the
    literal itself, because that part was written by whoever sent the message.
    """
    meta: list[bytes] = []
    literals: list[bytes] = []
    for item in data:
        if isinstance(item, tuple):
            meta.append(item[0])
            literals.append(item[1])
        elif item:
            meta.append(item)
    if len(literals) > 1:
        raise MailError("Unexpected FETCH response.")
    return b" ".join(meta), literals[0] if literals else None


def parse_flags(meta: bytes) -> frozenset[str]:
    match = re.search(rb"FLAGS \(([^)]*)\)", meta)
    if match is None:
        raise MailError("FETCH response has no flags.")
    return frozenset(flag.decode("ascii", "replace") for flag in match.group(1).split())


def parse_internaldate(meta: bytes) -> float | None:
    match = re.search(rb'INTERNALDATE "([^"]+)"', meta)
    if match is None:
        return None
    try:
        # 09-Nov-2026 10:00:00 +0000, and email.utils reads month names without a locale.
        text = match.group(1).decode("ascii").strip().replace("-", " ", 2)
        return parsedate_to_datetime(text).timestamp()
    except (ValueError, TypeError):
        return None


class ImapClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def _run(self, fn: Callable[[MailBox], Any], folder: str = "INBOX") -> Any:
        try:
            with MailBox(
                self._s.imap_host, port=self._s.imap_port, ssl_context=client_context()
            ).login(self._s.imap_user, self._s.imap_pass, initial_folder=folder) as mailbox:
                return fn(mailbox)
        except Exception as exc:
            raise MailError(f"IMAP error: {exc}") from exc

    async def run(self, fn: Callable[[MailBox], Any], folder: str = "INBOX") -> Any:
        return await anyio.to_thread.run_sync(lambda: self._run(fn, folder))

    # ---------- reads ----------

    async def folders(self) -> list[dict]:
        def op(mb: MailBox) -> list[dict]:
            out = []
            for info in mb.folder.list():
                try:
                    status = mb.folder.status(info.name, ["MESSAGES", "UNSEEN"])
                except Exception:  # some servers refuse STATUS on \Noselect
                    status = {}
                out.append(
                    {
                        "name": info.name,
                        "messages": status.get("MESSAGES"),
                        "unread": status.get("UNSEEN"),
                    }
                )
            return out

        return await self.run(op)

    @staticmethod
    def _criteria(
        unread_only: bool, sender: str | None, since: str | None, subject: str | None
    ):
        terms: dict[str, Any] = {}
        if unread_only:
            terms["seen"] = False
        if sender:
            terms["from_"] = sender
        if subject:
            terms["subject"] = subject
        if since:
            from datetime import date

            terms["date_gte"] = date.fromisoformat(since)
        return AND(**terms) if terms else "ALL"

    async def list_messages(
        self,
        folder: str,
        limit: int,
        unread_only: bool,
        sender: str | None,
        since: str | None,
        subject: str | None,
    ) -> list:
        criteria = self._criteria(unread_only, sender, since, subject)

        def op(mb: MailBox) -> list:
            # mark_seen defaults to True in imap-tools; listing a mailbox must
            # never silently mark it read.
            return list(
                mb.fetch(
                    criteria,
                    limit=limit,
                    reverse=True,
                    mark_seen=False,
                    headers_only=True,
                    bulk=True,
                )
            )

        return await self.run(op, folder)

    async def get_message(self, folder: str, uid: str, mark_seen: bool) -> Any:
        def op(mb: MailBox):
            found = list(mb.fetch(AND(uid=uid), mark_seen=mark_seen, limit=1))
            if not found:
                raise MailError(f"No message with uid {uid} in {folder}.")
            return found[0]

        return await self.run(op, folder)

    async def search_raw(self, folder: str, criteria: str, limit: int) -> list:
        def op(mb: MailBox) -> list:
            return list(
                mb.fetch(
                    criteria, limit=limit, reverse=True, mark_seen=False,
                    headers_only=True, bulk=True,
                )
            )

        return await self.run(op, folder)

    async def thread_of(self, folder: str, message_id: str, limit: int) -> list:
        """Best-effort conversation: anything referencing this Message-ID."""

        def op(mb: MailBox) -> list:
            found = list(
                mb.fetch(
                    f'(OR HEADER "In-Reply-To" "{message_id}" '
                    f'HEADER "References" "{message_id}")',
                    limit=limit, mark_seen=False, headers_only=True, bulk=True,
                )
            )
            origin = list(
                mb.fetch(f'HEADER "Message-ID" "{message_id}"',
                         limit=1, mark_seen=False, headers_only=True)
            )
            return origin + found

        return await self.run(op, folder)

    async def approval_candidates(
        self, sender: str, since: datetime.date, limit: int, max_bytes: int
    ) -> list[RawMessage]:
        """Newest INBOX messages that may be approvals, as the exact bytes on the server.

        The search only narrows things down. Every result is still verified by the caller,
        so a server or a sender that gets past this filter gains nothing.
        """

        def fetch(mb: MailBox, uid: str, items: str) -> tuple[bytes, bytes | None] | None:
            status, data = mb.client.uid("FETCH", uid, items)
            if status != "OK" or not data or data[0] is None:
                return None
            return parse_fetch_response(data)

        def op(mb: MailBox) -> list[RawMessage]:
            uids = mb.uids(AND(from_=sender, draft=False, date_gte=since))
            newest = sorted(uids, key=int, reverse=True)[:limit]
            found: list[RawMessage] = []
            for uid in newest:
                size = fetch(mb, uid, "(FLAGS INTERNALDATE RFC822.SIZE)")
                if size is None:
                    continue
                measured = re.search(rb"RFC822\.SIZE (\d+)", size[0])
                if measured is None or int(measured.group(1)) > max_bytes:
                    found.append(RawMessage(uid, None, parse_flags(size[0]), parse_internaldate(size[0])))
                    continue
                # BODY.PEEK: an approval check must not mark mail as read.
                full = fetch(mb, uid, "(FLAGS INTERNALDATE BODY.PEEK[])")
                if full is None or full[1] is None:
                    continue
                found.append(RawMessage(uid, full[1], parse_flags(full[0]), parse_internaldate(full[0])))
            return found

        return await self.run(op, "INBOX")

    # ---------- writes ----------

    async def set_seen(self, folder: str, uids: list[str], seen: bool) -> dict:
        def op(mb: MailBox) -> dict:
            mb.flag(uids, MailMessageFlags.SEEN, seen)
            return {"status": "ok", "uids": uids, "seen": seen}

        return await self.run(op, folder)

    async def move(self, folder: str, uids: list[str], destination: str) -> dict:
        def op(mb: MailBox) -> dict:
            if not mb.folder.exists(destination):
                raise MailError(
                    f"Destination folder {destination!r} does not exist. "
                    "List folders first."
                )
            mb.move(uids, destination)
            return {"status": "ok", "uids": uids, "moved_to": destination}

        return await self.run(op, folder)

    async def delete(self, folder: str, uids: list[str]) -> dict:
        def op(mb: MailBox) -> dict:
            mb.delete(uids)  # flags \Deleted and expunges: not recoverable
            return {"status": "ok", "uids": uids, "deleted_from": folder}

        return await self.run(op, folder)

    async def append_raw(self, folder: str, raw: bytes, flags: list[str]) -> dict:
        def op(mb: MailBox) -> dict:
            if not mb.folder.exists(folder):
                mb.folder.create(folder)
            mb.append(raw, folder, flag_set=flags)
            return {"status": "ok", "folder": folder}

        return await self.run(op, folder)
