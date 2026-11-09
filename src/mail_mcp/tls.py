"""TLS for the IMAP and SMTP clients.

imaplib and smtplib, given no context, build one that checks neither the certificate nor
the hostname. So without this, anyone on the path could read the mailbox password and
answer IMAP with messages of their choosing.
"""

from __future__ import annotations

import ssl


def client_context() -> ssl.SSLContext:
    return ssl.create_default_context()
