"""Attachments as something a model can read: text, and pictures it can look at.

A model cannot read a PDF, a Word file or a picture from base64 text, so mail_get_attachment
turns them into text (PDF page by page, Office and OpenDocument files with their tables as
rows, plain text formats decoded) and pictures into MCP image content. Everything runs in this
process: no network, no external service, no program started.

The files were written by whoever sent the mail. Parsing is bounded: the attachment size limit
applies before anything here runs, a ZIP container (Office, OpenDocument) may unpack to at most
MAX_UNZIPPED_BYTES, pictures to at most MAX_IMAGE_PIXELS (checked on the declared size, before
decoding), only the picture formats listed in IMAGE_FORMATS are opened, and extracted text stops
growing at the caller's cap (shared strings, repeated cells and repeated spaces cannot multiply it). A file that cannot be read gives Unreadable with a sentence of
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
MAX_UNZIPPED_BYTES = 10_000_000  # what an Office or OpenDocument file may unpack to
MAX_PDF_PAGES = 1000  # pages read from one PDF
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
    complete: bool = True  # False: extraction stopped at the text cap, text holds what came before


class _Full(Exception):
    """The extracted text reached its cap."""


class _Out:
    """Extracted lines, at most `cap` characters: lines past it are not built at all."""

    def __init__(self, cap: int) -> None:
        self.lines: list[str] = []
        self.left = cap
        self.full = False

    def reserve(self, size: int) -> None:
        """Account for text about to be built; stops extraction once the cap is passed."""
        if size > self.left:
            self.full = True
            raise _Full
        self.left -= size

    def line(self, text: str) -> None:
        if len(text) + 1 > self.left:  # keep what fits, so one huge page still shows its start
            self.lines.append(text[: max(self.left, 0)])
            self.left = 0
            self.full = True
            raise _Full
        self.left -= len(text) + 1
        self.lines.append(text)

    def text(self) -> str:
        return _lines("\n".join(self.lines))


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


def read(filename: str, content_type: str, payload: bytes, charset: str | None = None, max_chars: int = 200_000) -> Readable:
    """The attachment as text and pictures. Text of every format stops at ten times max_chars
    (at least a million characters; complete is then False), so a file cannot multiply its text
    without bound, and a normal file still gets its exact length."""
    cap = max(10 * max_chars, 1_000_000)
    kind = kind_of(filename, content_type, payload)
    if kind is None:
        raise Unreadable(f"{content_type or 'This file type'} is not a format this server can read.")
    if kind == "heic":
        raise Unreadable("HEIC pictures cannot be converted on this server.")
    try:
        if kind == "pdf":
            return _pdf(payload, _Out(cap))
        if kind == "image":
            return Readable(kind, images=[_picture(_open_image(payload))])
        if kind in ("docx", "xlsx", "pptx", "odt", "ods"):
            out = _Out(cap)
            try:
                _ZIPPED[kind](_Zip(payload), out)
            except _Full:
                pass
            return Readable(kind, text=out.text(), complete=not out.full)
        if kind == "html":
            return _capped(kind, html_to_text(_decode(payload, charset)), cap)
        if kind == "eml":
            return _capped(kind, _eml(payload), cap)
        return _capped(kind, _decode(payload, charset), cap)
    except Unreadable:
        raise
    except Exception as exc:  # a damaged file: the parser's own text stays out of the result
        logger.info("Attachment not readable (%s, %s)", kind, type(exc).__name__)
        raise Unreadable(f"The {kind.upper()} file could not be read; it may be damaged.") from None


# ---------------------------------------------------------------- text


def _capped(kind: str, text: str, cap: int) -> Readable:
    """Text of a format read whole (no ZIP, no repeats, so it is no longer than the file)."""
    if len(text) > cap:
        return Readable(kind, text=text[:cap], complete=False)
    return Readable(kind, text=text)


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
    return _lines(text.lstrip("\ufeff"))


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


def _too_big(width: int, height: int) -> Unreadable | None:
    if width * height > MAX_IMAGE_PIXELS:
        return Unreadable(f"The picture is {width} x {height} pixels, too big to convert.")
    return None


# A PDF page's /Rotate turns it clockwise for display; Pillow's ROTATE_* turn counterclockwise.
_PAGE_TURN = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}


def _picture(img: Image.Image, rotate: int = 0) -> tuple[bytes, str]:
    """A picture the model can look at: first frame, upright, long edge at most MAX_IMAGE_EDGE.
    rotate is the clockwise turn of the PDF page the picture fills (/Rotate)."""
    if error := _too_big(img.width, img.height):
        raise error
    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)
    img = ImageOps.exif_transpose(img)
    if turn := _PAGE_TURN.get(rotate % 360):
        img = img.transpose(turn)
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


def _pdf(payload: bytes, out: _Out) -> Readable:
    reader = PdfReader(io.BytesIO(payload))
    if reader.is_encrypted and not reader.decrypt(""):
        raise Unreadable("The PDF is protected by a password.")
    pages: list[dict] = []
    images: list[tuple[bytes, str]] = []
    notes: list[str] = []
    count = len(reader.pages)
    if count > MAX_PDF_PAGES:
        notes.append(f"The PDF has {count} pages; only the first {MAX_PDF_PAGES} were read.")
    try:
        for number in range(1, min(count, MAX_PDF_PAGES) + 1):
            page = reader.pages[number - 1]
            try:
                text = _lines(page.extract_text() or "")
            except Exception:
                text = ""
            if text:
                out.line(f"[Page {number}]\n{text}\n")
                pages.append({"page": number, "has_text": True})
                continue
            out.line(f"[Page {number}: no text layer, probably a scan]\n")
            entry = {"page": number, "has_text": False, "image": False}
            pages.append(entry)
            if len(images) >= MAX_IMAGES:
                notes.append(f"Page {number} has no text; its picture was not returned, at most {MAX_IMAGES} pictures are.")
                continue
            try:
                scan = _largest_image(page)
            except Unreadable as exc:
                notes.append(f"Page {number}: {exc}")
                continue
            if scan is None:
                notes.append(f"Page {number} has no text and no picture this server can extract.")
                continue
            try:
                images.append(_picture(scan, _page_rotation(page)))
                entry["image"] = True
            except Unreadable as exc:
                notes.append(f"Page {number}: {exc}")
    except _Full:
        pass
    return Readable("pdf", text=out.text(), images=images, pages=pages, notes=notes, complete=not out.full)


def _page_rotation(page) -> int:
    """The page's /Rotate, a multiple of 90 degrees clockwise; anything else counts as 0."""
    try:
        rotate = int(page.get("/Rotate", 0))
    except Exception:
        return 0
    return rotate % 360 if rotate % 90 == 0 else 0


def _largest_image(page) -> Image.Image | None:
    """The biggest picture placed on a page: a scanner puts the whole page into one. Sizes are
    compared as the PDF declares them, and only the chosen picture is decoded."""
    try:
        xobjects = page["/Resources"]["/XObject"].get_object()
        declared = []
        for name, ref in xobjects.items():
            obj = ref.get_object()
            if obj.get("/Subtype") == "/Image":
                declared.append((int(obj.get("/Width", 0)) * int(obj.get("/Height", 0)), name, obj))
    except Exception:
        return None
    if not declared:
        return None
    _area, name, obj = max(declared, key=lambda item: item[0])
    if error := _too_big(int(obj.get("/Width", 0)), int(obj.get("/Height", 0))):
        raise error
    try:
        return page.images[name].image
    except Exception:
        return None


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


def _docx(z: _Zip, out: _Out) -> None:
    body = next(_all(z.xml("word/document.xml"), "body"), None)

    def block(el: ET.Element) -> None:
        name = _local(el.tag)
        if name == "p":
            out.line(_word_paragraph(el))
        elif name == "tbl":
            for row in _children(el, "tr"):
                cells = [" ".join(_word_paragraph(p) for p in _all(cell, "p")).strip() for cell in _children(row, "tc")]
                out.line(" | ".join(cells))
            out.line("")
        elif name in ("sdt", "sdtContent", "customXml"):
            for child in el:
                block(child)

    for el in body if body is not None else []:
        block(el)


_CELL = re.compile(r"([A-Z]+)")


def _column(ref: str | None) -> int | None:
    match = _CELL.match(ref or "")
    if not match:
        return None
    number = 0
    for letter in match.group(1):
        number = number * 26 + ord(letter) - 64
    return number - 1


def _xlsx(z: _Zip, out: _Out) -> None:
    shared = []
    strings = z.xml("xl/sharedStrings.xml", required=False)
    for si in _children(strings, "si") if strings is not None else []:
        shared.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
    workbook = z.xml("xl/workbook.xml")
    rels = z.xml("xl/_rels/workbook.xml.rels", required=False)
    targets = {rel.get("Id"): rel.get("Target", "") for rel in (rels if rels is not None else [])}
    for sheet in _all(workbook, "sheet"):
        rid = next((value for key, value in sheet.attrib.items() if _local(key) == "id"), None)
        target = targets.get(rid, "")
        path = target.lstrip("/") if target.startswith("/") else os.path.normpath("xl/" + target).replace(os.sep, "/")
        data = z.xml(path, required=False) if target else None
        if data is None:
            continue
        out.line(f"## Sheet: {sheet.get('name', '')}")
        for row in _all(data, "row"):
            cells: list[str] = []
            size = 0
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
                pad = column - len(cells) if column is not None and len(cells) < column <= len(cells) + 1000 else 0
                # A shared string can be used by any number of cells: count it each time.
                size += pad + len(text) + 1
                if size > out.left:
                    out.reserve(size)
                cells += [""] * pad
                cells.append(text)
            line = "\t".join(cells).rstrip("\t")
            if line:
                out.line(line)
        out.line("")


def _slide_order(z: _Zip) -> list[str]:
    """The slides as the presentation shows them: p:sldIdLst in ppt/presentation.xml through its
    relationships. The file names (slide1.xml ...) keep the order slides were made in, so they
    are used only when the presentation has no slide list."""
    names = set(z.names())
    numbered = sorted(
        (int(m.group(1)), name) for name in names if (m := re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name))
    )
    by_name = [name for _number, name in numbered]
    presentation = z.xml("ppt/presentation.xml", required=False) if "ppt/presentation.xml" in names else None
    rels = z.xml("ppt/_rels/presentation.xml.rels", required=False) if presentation is not None else None
    if presentation is None or rels is None:
        return by_name
    targets = {rel.get("Id"): rel.get("Target", "") for rel in rels}
    shown: list[str] = []
    for slide in _all(presentation, "sldId"):
        rid = next((value for key, value in slide.attrib.items() if _local(key) == "id" and key.startswith("{")), None)
        target = targets.get(rid, "")
        path = target.lstrip("/") if target.startswith("/") else os.path.normpath("ppt/" + target).replace(os.sep, "/")
        if target and path in names and path not in shown:
            shown.append(path)
    return shown or by_name


def _pptx(z: _Zip, out: _Out) -> None:
    for number, name in enumerate(_slide_order(z), start=1):
        out.line(f"## Slide {number}")
        for p in _all(z.xml(name), "p"):
            text = "".join(t.text or "" for t in p.iter() if _local(t.tag) == "t")
            if text.strip():
                out.line(text)
        out.line("")


def _odf_text(el: ET.Element, limit: int) -> str:
    """The text of a paragraph, built only until it is longer than limit: a text:s element
    stands for up to 100 spaces, so a short paragraph can mean a long text."""
    out: list[str] = []
    size = 0

    def add(text: str) -> None:
        nonlocal size
        if size <= limit:
            out.append(text)
            size += len(text)

    def walk(node: ET.Element) -> None:
        add(node.text or "")
        for child in node:
            if size > limit:
                return
            name = _local(child.tag)
            if name == "s":
                try:
                    add(" " * min(int(child.get("{urn:oasis:names:tc:opendocument:xmlns:text:1.0}c", "1")), 100))
                except ValueError:
                    add(" ")
            elif name == "tab":
                add("\t")
            elif name == "line-break":
                add("\n")
            elif name not in ("annotation", "note-citation"):
                walk(child)
            add(child.tail or "")

    walk(el)
    return "".join(out)


_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"


def _repeat(el: ET.Element, attribute: str) -> int:
    try:
        return max(1, min(int(el.get(_TABLE + attribute, "1")), 100))
    except ValueError:
        return 1


def _odf_row_elements(el: ET.Element):
    """The rows of one table, not those of tables nested in its cells."""
    for child in el:
        name = _local(child.tag)
        if name == "table-row":
            yield child
        elif name in ("table-rows", "table-header-rows", "table-row-group"):
            yield from _odf_row_elements(child)


def _odf_rows(table: ET.Element, separator: str, out: _Out) -> None:
    for row in _odf_row_elements(table):
        cells: list[str] = []
        size = 0
        for cell in row:
            if _local(cell.tag) not in ("table-cell", "covered-table-cell"):
                continue
            paragraphs: list[str] = []
            left = out.left - size
            for p in _children(cell, "p"):
                paragraphs.append(_odf_text(p, left))
                left -= len(paragraphs[-1]) + 1
                if left < 0:  # the cell alone is longer than what is left: the row does not fit
                    out.reserve(out.left + 1)
            text = "\n".join(paragraphs).strip()
            repeat = _repeat(cell, "number-columns-repeated")
            size += (len(text) + len(separator)) * repeat
            if size > out.left:
                out.reserve(size)
            cells += [text] * repeat
        while cells and not cells[-1]:
            cells.pop()
        if cells:
            line = separator.join(cells)
            for _ in range(_repeat(row, "number-rows-repeated")):
                out.line(line)


def _odt(z: _Zip, out: _Out) -> None:
    def block(el: ET.Element) -> None:
        for child in el:
            name = _local(child.tag)
            if name in ("p", "h"):
                out.line(_odf_text(child, out.left))
            elif name == "table":
                _odf_rows(child, " | ", out)
                out.line("")
            elif name in ("list", "list-item", "section", "text", "body"):
                block(child)

    block(z.xml("content.xml"))


def _ods(z: _Zip, out: _Out) -> None:
    for spreadsheet in _all(z.xml("content.xml"), "spreadsheet"):
        for table in _children(spreadsheet, "table"):
            out.line(f"## Sheet: {table.get(_TABLE + 'name', '')}")
            _odf_rows(table, "\t", out)
            out.line("")


_ZIPPED = {"docx": _docx, "xlsx": _xlsx, "pptx": _pptx, "odt": _odt, "ods": _ods}
