"""IMAP access.

`imap-tools` is synchronous and IMAP connections go stale when idle, so every
operation opens its own short-lived connection inside a worker thread. That
costs a login per call and buys not having to nurse a long-lived socket back
to life mid-request.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import anyio
from imap_tools import AND, MailBox, MailMessageFlags

from .settings import Settings
from .tls import client_context

logger = logging.getLogger(__name__)


class MailError(RuntimeError):
    pass


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
