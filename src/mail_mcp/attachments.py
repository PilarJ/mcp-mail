"""Attachments as something a model can read: text, and pictures it can look at.

A model cannot read a PDF, a Word file or a picture from base64 text, so mail_get_attachment
turns them into text (PDF page by page, Office and OpenDocument files with their tables as
rows, plain text formats decoded) and pictures into MCP image content. Everything runs in this
process: no network, no external service, no program started.

The files were written by whoever sent the mail. Parsing is bounded: the attachment size limit
applies before anything here runs, a ZIP container (Office, OpenDocument) may unpack to at most
MAX_UNZIPPED_BYTES, pictures to at most MAX_IMAGE_PIXELS, and only the picture formats listed
in IMAGE_FORMATS are opened. A file that cannot be read gives Unreadable with a sentence of
ours, never the library's text.
"""

from __future__ import annotations

import email
import io
import logging
import os
import re
import warnings
import zipfile
from dataclasses import dataclass, field
from email import policy
from xml.etree import ElementTree as ET

from defusedxml.ElementTree import fromstring as parse_xml

from PIL import Image, ImageOps
from pypdf import PdfReader

from .render import html_to_text

logger = logging.getLogger(__name__)

MAX_IMAGE_EDGE = 1568  # long edge of a returned picture, in pixels
MAX_IMAGES = 20  # pictures returned for one attachment (scanned PDF pages)
MAX_IMAGE_PIXELS = 60_000_000  # bigger pictures are refused before they are decoded
MAX_UNZIPPED_BYTES = 50_000_000  # what an Office or OpenDocument file may unpack to
IMAGE_FORMATS = ("PNG", "JPEG", "GIF", "WEBP", "TIFF", "BMP")

KINDS_BY_EXTENSION = {
    ".pdf": "pdf",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".webp": "image",
    ".tif": "image", ".tiff": "image", ".bmp": "image",
    ".heic": "heic", ".heif": "heic",
    ".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx", ".odt": "odt", ".ods": "ods",
    ".txt": "text", ".csv": "text", ".tsv": "text", ".xml": "text", ".json": "text",
    ".isdoc": "text", ".md": "text", ".log": "text",
    ".html": "html", ".htm": "html",
    ".eml": "eml",
}
KINDS_BY_TYPE = {
    "application/pdf": "pdf",
    "image/heic": "heic", "image/heif": "heic",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "text/html": "html",
    "message/rfc822": "eml",
    "application/json": "text", "application/xml": "text",
}


class Unreadable(Exception):
    """The attachment cannot be turned into text or a picture. The text is ours."""


@dataclass
class Readable:
    kind: str
    text: str = ""
    images: list[tuple[bytes, str]] = field(default_factory=list)  # (data, mime type)
    pages: list[dict] | None = None
    notes: list[str] = field(default_factory=list)


def kind_of(filename: str, content_type: str, payload: bytes) -> str | None:
    if payload.startswith(b"%PDF-"):
        return "pdf"
    content_type = (content_type or "").lower()
    found = KINDS_BY_EXTENSION.get(os.path.splitext((filename or "").lower())[1]) or KINDS_BY_TYPE.get(content_type)
    if found:
        return found
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("text/"):
        return "text"
    return None


def read(filename: str, content_type: str, payload: bytes, charset: str | None = None) -> Readable:
    kind = kind_of(filename, content_type, payload)
    if kind is None:
        raise Unreadable(f"{content_type or 'This file type'} is not a format this server can read.")
    if kind == "heic":
        raise Unreadable("HEIC pictures cannot be converted on this server.")
    try:
        if kind == "pdf":
            return _pdf(payload)
        if kind == "image":
            return Readable(kind, images=[_picture(_open_image(payload))])
        if kind in ("docx", "xlsx", "pptx", "odt", "ods"):
            return Readable(kind, text=_ZIPPED[kind](_Zip(payload)))
        if kind == "html":
            return Readable(kind, text=html_to_text(_decode(payload, charset)))
        if kind == "eml":
            return Readable(kind, text=_eml(payload))
        return Readable(kind, text=_decode(payload, charset))
    except Unreadable:
        raise
    except Exception as exc:  # a damaged file: the parser's own text stays out of the result
        logger.info("Attachment not readable (%s, %s)", kind, type(exc).__name__)
        raise Unreadable(f"The {kind.upper()} file could not be read; it may be damaged.") from None


# ---------------------------------------------------------------- text


def _decode(payload: bytes, charset: str | None) -> str:
    for encoding in (charset, "utf-8", "cp1250"):
        if not encoding:
            continue
        try:
            text = payload.decode(encoding)
            break
        except (LookupError, UnicodeDecodeError):
            continue
    else:
        text = payload.decode("latin-1")
    return _lines(text.lstrip("﻿"))


def _lines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _eml(payload: bytes) -> str:
    msg = email.message_from_bytes(payload, policy=policy.default)
    head = [f"{name}: {msg[name]}" for name in ("From", "To", "Cc", "Date", "Subject") if msg[name]]
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        try:
            text = body.get_content()
        except Exception:
            text = (body.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if body.get_content_subtype() == "html":
            text = html_to_text(text)
    names = [part.get_filename() or part.get_content_type() for part in msg.iter_attachments()]
    tail = f"\n\nAttachments of this message: {', '.join(names)}" if names else ""
    return _lines("\n".join(head) + "\n\n" + _lines(text) + tail)


# ---------------------------------------------------------------- pictures


def _open_image(payload: bytes) -> Image.Image:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        img = Image.open(io.BytesIO(payload), formats=IMAGE_FORMATS)  # reads the header only
    return img


def _picture(img: Image.Image) -> tuple[bytes, str]:
    """A picture the model can look at: first frame, upright, long edge at most MAX_IMAGE_EDGE."""
    if img.width * img.height > MAX_IMAGE_PIXELS:
        raise Unreadable(f"The picture is {img.width} x {img.height} pixels, too big to convert.")
    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)
    img = ImageOps.exif_transpose(img)
    img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
    alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    if img.mode.startswith("I") or img.mode == "F":
        img = img.convert("L")
    out = io.BytesIO()
    if alpha:
        img.convert("RGBA").save(out, "PNG", optimize=True)
        return out.getvalue(), "image/png"
    img.convert("RGB").save(out, "JPEG", quality=85)
    return out.getvalue(), "image/jpeg"


# ---------------------------------------------------------------- PDF


def _pdf(payload: bytes) -> Readable:
    reader = PdfReader(io.BytesIO(payload))
    if reader.is_encrypted and not reader.decrypt(""):
        raise Unreadable("The PDF is protected by a password.")
    parts: list[str] = []
    pages: list[dict] = []
    images: list[tuple[bytes, str]] = []
    notes: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = _lines(page.extract_text() or "")
        except Exception:
            text = ""
        if text:
            parts.append(f"[Page {number}]\n{text}")
            pages.append({"page": number, "has_text": True})
            continue
        parts.append(f"[Page {number}: no text layer, probably a scan]")
        entry = {"page": number, "has_text": False}
        scan = _largest_image(page)
        if scan is None:
            entry["image"] = False
            notes.append(f"Page {number} has no text and no picture this server can extract.")
        elif len(images) >= MAX_IMAGES:
            entry["image"] = False
            notes.append(f"Page {number} has no text; its picture was not returned, at most {MAX_IMAGES} pictures are.")
        else:
            try:
                images.append(_picture(scan))
                entry["image"] = True
            except Unreadable as exc:
                entry["image"] = False
                notes.append(f"Page {number}: {exc}")
        pages.append(entry)
    return Readable("pdf", text="\n\n".join(parts), images=images, pages=pages, notes=notes)


def _largest_image(page) -> Image.Image | None:
    """The biggest picture on a page: a scanner puts the whole page into one."""
    best = None
    try:
        for found in page.images:
            img = found.image
            if img is not None and (best is None or img.width * img.height > best.width * best.height):
                best = img
    except Exception:
        return best
    return best


# ---------------------------------------------------------------- Office and OpenDocument


class _Zip:
    """A ZIP container read within MAX_UNZIPPED_BYTES in total."""

    def __init__(self, payload: bytes) -> None:
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile:
            raise Unreadable("The file is not a valid Office or OpenDocument file.") from None
        self.left = MAX_UNZIPPED_BYTES

    def names(self) -> list[str]:
        return self.zip.namelist()

    def xml(self, name: str, required: bool = True) -> ET.Element | None:
        try:
            info = self.zip.getinfo(name)
        except KeyError:
            if required:
                raise Unreadable(f"The file has no {name}; it is not a valid Office or OpenDocument file.") from None
            return None
        if info.file_size > self.left:
            raise Unreadable("The file unpacks to more than this server reads.")
        with self.zip.open(info) as member:
            data = member.read(self.left + 1)
        if len(data) > self.left:
            raise Unreadable("The file unpacks to more than this server reads.")
        self.left -= len(data)
        # Office and OpenDocument files never need a DTD; one is refused (entity tricks).
        return parse_xml(data, forbid_dtd=True)


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _children(el: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in el if _local(child.tag) == name]


def _all(el: ET.Element, name: str):
    return (child for child in el.iter() if _local(child.tag) == name)


def _word_paragraph(p: ET.Element) -> str:
    out = []
    for el in p.iter():
        name = _local(el.tag)
        if name == "t":
            out.append(el.text or "")
        elif name == "tab":
            out.append("\t")
        elif name in ("br", "cr"):
            out.append("\n")
    return "".join(out)


def _docx(z: _Zip) -> str:
    body = next(_all(z.xml("word/document.xml"), "body"), None)
    lines: list[str] = []

    def block(el: ET.Element) -> None:
        name = _local(el.tag)
        if name == "p":
            lines.append(_word_paragraph(el))
        elif name == "tbl":
            for row in _children(el, "tr"):
                cells = [" ".join(_word_paragraph(p) for p in _all(cell, "p")).strip() for cell in _children(row, "tc")]
                lines.append(" | ".join(cells))
            lines.append("")
        elif name in ("sdt", "sdtContent", "customXml"):
            for child in el:
                block(child)

    for el in body if body is not None else []:
        block(el)
    return _lines("\n".join(lines))


_CELL = re.compile(r"([A-Z]+)")


def _column(ref: str | None) -> int | None:
    match = _CELL.match(ref or "")
    if not match:
        return None
    number = 0
    for letter in match.group(1):
        number = number * 26 + ord(letter) - 64
    return number - 1


def _xlsx(z: _Zip) -> str:
    shared = []
    strings = z.xml("xl/sharedStrings.xml", required=False)
    for si in _children(strings, "si") if strings is not None else []:
        shared.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
    workbook = z.xml("xl/workbook.xml")
    rels = z.xml("xl/_rels/workbook.xml.rels", required=False)
    targets = {rel.get("Id"): rel.get("Target", "") for rel in (rels if rels is not None else [])}
    lines: list[str] = []
    for sheet in _all(workbook, "sheet"):
        rid = next((value for key, value in sheet.attrib.items() if _local(key) == "id"), None)
        target = targets.get(rid, "")
        path = target.lstrip("/") if target.startswith("/") else os.path.normpath("xl/" + target).replace(os.sep, "/")
        data = z.xml(path, required=False) if target else None
        if data is None:
            continue
        lines.append(f"## Sheet: {sheet.get('name', '')}")
        for row in _all(data, "row"):
            cells: list[str] = []
            for cell in _children(row, "c"):
                value = next(iter(_children(cell, "v")), None)
                kind = cell.get("t")
                if kind == "s":
                    try:
                        text = shared[int(value.text)]
                    except (AttributeError, ValueError, IndexError, TypeError):
                        text = ""
                elif kind == "inlineStr":
                    text = "".join(t.text or "" for t in cell.iter() if _local(t.tag) == "t")
                elif kind == "b":
                    text = "TRUE" if value is not None and value.text == "1" else "FALSE"
                else:
                    text = value.text or "" if value is not None else ""
                column = _column(cell.get("r"))
                if column is not None and len(cells) < column <= len(cells) + 1000:
                    cells += [""] * (column - len(cells))
                cells.append(text)
            line = "\t".join(cells).rstrip("\t")
            if line:
                lines.append(line)
        lines.append("")
    return _lines("\n".join(lines))


def _pptx(z: _Zip) -> str:
    slides = sorted(
        (int(m.group(1)), name) for name in z.names() if (m := re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name))
    )
    lines: list[str] = []
    for number, name in slides:
        lines.append(f"## Slide {number}")
        for p in _all(z.xml(name), "p"):
            text = "".join(t.text or "" for t in p.iter() if _local(t.tag) == "t")
            if text.strip():
                lines.append(text)
        lines.append("")
    return _lines("\n".join(lines))


def _odf_text(el: ET.Element) -> str:
    out = [el.text or ""]
    for child in el:
        name = _local(child.tag)
        if name == "s":
            try:
                out.append(" " * min(int(child.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}c", "1")), 100))
            except ValueError:
                out.append(" ")
        elif name == "tab":
            out.append("\t")
        elif name == "line-break":
            out.append("\n")
        elif name not in ("annotation", "note-citation"):
            out.append(_odf_text(child))
        out.append(child.tail or "")
    return "".join(out)


_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"


def _repeat(el: ET.Element, attribute: str) -> int:
    try:
        return max(1, min(int(el.get(_TABLE + attribute, "1")), 100))
    except ValueError:
        return 1


def _odf_rows(table: ET.Element, separator: str) -> list[str]:
    lines = []
    for row in _all(table, "table-row"):
        cells: list[str] = []
        for cell in row:
            if _local(cell.tag) not in ("table-cell", "covered-table-cell"):
                continue
            text = "\n".join(_odf_text(p) for p in _children(cell, "p")).strip()
            cells += [text] * _repeat(cell, "number-columns-repeated")
        while cells and not cells[-1]:
            cells.pop()
        if cells:
            lines += [separator.join(cells)] * _repeat(row, "number-rows-repeated")
    return lines


def _odt(z: _Zip) -> str:
    lines: list[str] = []

    def block(el: ET.Element) -> None:
        for child in el:
            name = _local(child.tag)
            if name in ("p", "h"):
                lines.append(_odf_text(child))
            elif name == "table":
                lines.extend(_odf_rows(child, " | "))
                lines.append("")
            elif name in ("list", "list-item", "section", "text", "body"):
                block(child)

    block(z.xml("content.xml"))
    return _lines("\n".join(lines))


def _ods(z: _Zip) -> str:
    lines: list[str] = []
    for table in _all(z.xml("content.xml"), "table"):
        lines.append(f"## Sheet: {table.get(_TABLE + 'name', '')}")
        lines.extend(_odf_rows(table, "\t"))
        lines.append("")
    return _lines("\n".join(lines))


_ZIPPED = {"docx": _docx, "xlsx": _xlsx, "pptx": _pptx, "odt": _odt, "ods": _ods}
