"""Which attachments a message has, from its IMAP BODYSTRUCTURE (RFC 3501 7.4.2).

Lists fetch headers only, so the parsed message there has no parts and cannot say whether it
carries attachments. BODYSTRUCTURE describes every part without downloading any of it and,
unlike BODY[], never sets \\Seen. A part counts as an attachment by the same rule imap-tools
applies to a full message: it has a file name, a Content-ID, or is a forwarded message
(message/rfc822), whose own attachments are listed after it.

Everything in a BODYSTRUCTURE was written by the sender. Nothing here raises on what a server
sends: anything that cannot be read is left out, and the caller reports it as unknown.
"""

from __future__ import annotations

import re
from email.header import decode_header, make_header
from email.utils import collapse_rfc2231_value, decode_params, unquote

_TOKEN = re.compile(rb'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|\{(\d+)\}$|([^\s()"]+))')
_UNESCAPE = re.compile(rb"\\(.)")
_OPEN, _CLOSE = object(), object()
_MAX_DEPTH = 64


def _tokens(data: list):
    """Atoms, strings, ( and ) of an imaplib FETCH answer. A literal arrives as a tuple
    (line ending in {n}, its bytes); the line continues in the next item."""
    for item in data:
        if isinstance(item, tuple) and len(item) == 2:
            yield from _scan(item[0])
            yield item[1]
        elif isinstance(item, bytes):
            yield from _scan(item)


def _scan(line: bytes):
    pos = 0
    while pos < len(line):
        match = _TOKEN.match(line, pos)
        if match is None or match.end() == pos:
            if line[pos:].strip():
                raise ValueError("unreadable FETCH answer")
            return
        pos = match.end()
        opened, closed, quoted, _literal, atom = match.groups()
        if opened:
            yield _OPEN
        elif closed:
            yield _CLOSE
        elif quoted is not None:
            yield _UNESCAPE.sub(rb"\1", quoted)
        elif atom is not None:
            yield None if atom.upper() == b"NIL" else atom
        # a literal marker: its bytes are the next token


def _parse(data: list) -> list:
    """The answer as nested lists of bytes (None for NIL)."""
    top: list = []
    stack = [top]
    for token in _tokens(data):
        if token is _OPEN:
            if len(stack) > _MAX_DEPTH:
                raise ValueError("BODYSTRUCTURE nested too deeply")
            inner: list = []
            stack[-1].append(inner)
            stack.append(inner)
        elif token is _CLOSE:
            if len(stack) == 1:
                raise ValueError("unbalanced FETCH answer")
            stack.pop()
        else:
            stack[-1].append(token)
    if len(stack) != 1:
        raise ValueError("unbalanced FETCH answer")
    return top


def _text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else ""


def _params(value) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        return []
    items = [_text(v) for v in value]
    return [(items[i].lower(), items[i + 1]) for i in range(0, len(items) - 1, 2)]


def _decoded(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # an encoded word with a charset Python does not know
        return value


def _filename(params: list[tuple[str, str]]) -> str:
    """filename (or name) after RFC 2231 continuations and charsets and RFC 2047 words."""
    if not params:
        return ""
    quoted = [(name, f'"{value}"' if "*" not in name else value) for name, value in params]
    try:
        found = dict(decode_params([("", "")] + quoted)[1:])
    except Exception:
        found = dict(params)
    for key in ("filename", "name"):
        if key in found:
            try:
                value = collapse_rfc2231_value(found[key])
            except Exception:
                value = str(found[key])
            return _decoded(unquote(value))
    return ""


def _names(body, depth: int = 0) -> list[str]:
    if not isinstance(body, list) or not body or depth > _MAX_DEPTH:
        return []
    if isinstance(body[0], list):  # multipart: the parts, then the subtype
        found: list[str] = []
        for part in body:
            if isinstance(part, list):
                found += _names(part, depth + 1)
        return found
    kind = f"{_text(body[0]).lower()}/{_text(body[1] if len(body) > 1 else None).lower()}"
    # The disposition comes after the fields of the part's type (RFC 3501 7.4.2).
    at = 11 if kind == "message/rfc822" else 9 if kind.startswith("text/") else 8
    disposition = body[at] if len(body) > at and isinstance(body[at], list) else None
    params = _params(disposition[1] if disposition and len(disposition) > 1 else None)
    name = _filename(params) or _filename([p for p in _params(body[2] if len(body) > 2 else None) if p[0].startswith("name")])
    content_id = body[3] if len(body) > 3 else None
    found = []
    if name or content_id is not None or kind == "message/rfc822":
        found.append(name or f"(no name, {kind})")
    if kind == "message/rfc822" and len(body) > 8:
        found += _names(body[8], depth + 1)
    return found


def attachments_by_uid(data: list) -> dict[str, list[str]]:
    """uid -> attachment names, for each message of a UID FETCH ... (BODYSTRUCTURE) answer
    that has one. A message the server described without BODYSTRUCTURE is missing."""
    try:
        parsed = _parse([item for item in data if item])
    except ValueError:
        return {}
    found: dict[str, list[str]] = {}
    for item in parsed:
        if not isinstance(item, list):
            continue  # the sequence number and FETCH
        pairs = {_text(item[i]).upper(): item[i + 1] for i in range(0, len(item) - 1, 2) if isinstance(item[i], bytes)}
        uid = _text(pairs.get("UID"))
        if uid.isdigit() and isinstance(pairs.get("BODYSTRUCTURE"), list):
            found[uid] = _names(pairs["BODYSTRUCTURE"])
    return found
