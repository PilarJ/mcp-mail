"""IMAP access.

`imap-tools` is synchronous and IMAP connections go stale when idle, so every
operation opens its own short-lived connection inside a worker thread. That
costs a login per call and buys not having to nurse a long-lived socket back
to life mid-request.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import anyio
from imap_tools import AND, MailBox, MailMessageFlags
from imap_tools.utils import quote

from .settings import Settings

logger = logging.getLogger(__name__)

DEFAULT_SENT_FOLDER = "Sent Messages"


def _charset(criteria: Any) -> str:
    """IMAP search strings are 8-bit text: anything beyond ASCII needs CHARSET UTF-8,
    otherwise imap-tools fails with "'ascii' codec can't encode character"."""
    return "US-ASCII" if str(criteria).isascii() else "UTF-8"


class MailError(RuntimeError):
    pass


class UnsafeInput(MailError):
    """A caller-supplied string that must never reach the IMAP connection."""


def reject_control_chars(**values: str | None) -> None:
    """Refuse any string that holds a control character (CR, LF, NUL, ESC, DEL, C1 ...).

    imaplib joins command arguments with spaces and writes `tag NAME args CRLF` without looking
    inside them, so a CR or LF in a search string is a command boundary: the rest of the string
    runs as another IMAP command with the mailbox's credentials, past the delete switch and the
    confirm gate. The input is refused as it is, never stripped or normalised into something
    valid, and this runs before a connection is opened. Ordinary text, diacritics included,
    passes.
    """
    for name, value in values.items():
        if value is not None and any(unicodedata.category(ch) == "Cc" for ch in value):
            raise UnsafeInput(
                f"Refused: {name} contains a control character (CR, LF, NUL or similar). "
                "Nothing was sent to the mailbox."
            )


class ImapClient:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def _run(self, fn: Callable[[MailBox], Any], folder: str = "INBOX") -> Any:
        try:
            with MailBox(self._s.imap_host, port=self._s.imap_port).login(
                self._s.imap_user, self._s.imap_pass, initial_folder=folder
            ) as mailbox:
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
        reject_control_chars(sender=sender, subject=subject)
        criteria = self._criteria(unread_only, sender, since, subject)

        def op(mb: MailBox) -> list:
            # mark_seen defaults to True in imap-tools; listing a mailbox must
            # never silently mark it read.
            return list(
                mb.fetch(
                    criteria,
                    _charset(criteria),
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
        reject_control_chars(criteria=criteria)

        def op(mb: MailBox) -> list:
            return list(
                mb.fetch(
                    criteria, _charset(criteria), limit=limit, reverse=True,
                    mark_seen=False, headers_only=True, bulk=True,
                )
            )

        return await self.run(op, folder)

    async def delivery_dates(self, folder: str, uids: list[str]) -> dict[str, datetime]:
        """When the server received each message: the fallback date for mail whose own
        Date header is missing. Best effort, an empty answer is fine."""
        if not uids:
            return {}

        def op(mb: MailBox) -> dict[str, datetime]:
            typ, data = mb.client.uid("FETCH", ",".join(uids), "(INTERNALDATE)")
            found: dict[str, datetime] = {}
            for item in data if typ == "OK" else []:
                line = item[0] if isinstance(item, tuple) else item
                if not isinstance(line, bytes):
                    continue
                uid = re.search(rb"UID (\d+)", line)
                stamp = re.search(rb'INTERNALDATE "([^"]+)"', line)
                if uid and stamp:
                    found[uid.group(1).decode()] = parsedate_to_datetime(stamp.group(1).decode())
            return found

        return await self.run(op, folder)

    async def thread_of(self, folder: str, message_id: str, limit: int) -> list:
        """Best-effort conversation: anything referencing this Message-ID."""
        reject_control_chars(message_id=message_id)
        quoted = quote(message_id)  # one IMAP string, whatever quotes or backslashes it holds

        def op(mb: MailBox) -> list:
            found = list(
                mb.fetch(
                    f'(OR HEADER "In-Reply-To" {quoted} HEADER "References" {quoted})',
                    limit=limit, mark_seen=False, headers_only=True, bulk=True,
                )
            )
            origin = list(
                mb.fetch(f'HEADER "Message-ID" {quoted}',
                         limit=1, mark_seen=False, headers_only=True)
            )
            return origin + found

        return await self.run(op, folder)

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

    async def save_sent_copy(self, raw: bytes) -> str:
        """File a copy of a mail that was just sent. Returns the folder it went to.

        Order of choice: SENT_FOLDER, the folder the server marks \\Sent, "Sent Messages".
        The copy is marked read and dated now, as a mail client would.
        """

        def op(mb: MailBox) -> str:
            folder = self._s.sent_folder or next(
                (i.name for i in mb.folder.list() if "\\Sent" in i.flags), DEFAULT_SENT_FOLDER
            )
            if not mb.folder.exists(folder):
                mb.folder.create(folder)
            mb.append(raw, folder, flag_set=["\\Seen"])
            return folder

        return await self.run(op)

    async def append_raw(self, folder: str, raw: bytes, flags: list[str]) -> dict:
        def op(mb: MailBox) -> dict:
            if not mb.folder.exists(folder):
                mb.folder.create(folder)
            mb.append(raw, folder, flag_set=flags)
            return {"status": "ok", "folder": folder}

        return await self.run(op, folder)
