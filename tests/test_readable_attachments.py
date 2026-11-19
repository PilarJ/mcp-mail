"""Agents read whole mails and every attachment (#13).

mail_get_attachment returns what a model can read by default: the text of a PDF page by page
(and the picture of a page without text, a scan), images as MCP image content, the text of
Office and OpenDocument files, and plain text formats. format="base64" still gives the original
bytes. mail_list and mail_search report the real attachments and size from BODYSTRUCTURE and
RFC822.SIZE without downloading a body and without marking anything read.

The real imaplib and imap-tools run against `AttachmentServer`, a fake IMAP server behind the
socket (see imap_wire.py). The fixture files are built here, nothing is downloaded.
"""

from __future__ import annotations

import base64
import io
import re
import zipfile
from email.message import EmailMessage

import pytest
from fastmcp import Client, FastMCP
from mcp.types import ImageContent, TextContent
from PIL import Image

from mail_mcp import imap as imap_module
from mail_mcp import tools
from mail_mcp.bodystructure import attachments_by_uid
from mail_mcp.settings import Settings

from .imap_wire import Command, WireServer

pytestmark = pytest.mark.anyio

SEEN = "\\Seen"


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------- fixture files


def text_pdf(pages: list[str]) -> bytes:
    """A small valid PDF with one line of Helvetica text per page."""
    objects: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font_id = 3 + 2 * len(pages)
    for i, line in enumerate(pages):
        content = f"BT /F1 18 Tf 72 720 Td ({line}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def picture(width: int, height: int, color=(200, 30, 30)) -> Image.Image:
    img = Image.new("RGB", (width, height), "white")
    for x in range(0, width, 7):
        for y in range(height // 3, height // 3 + 20):
            img.putpixel((x, y), color)
    return img


def scanned_pdf() -> bytes:
    """What a scanner writes: every page is one picture, no text layer."""
    buf = io.BytesIO()
    picture(1240, 1754).save(buf, "PDF", resolution=150)
    return buf.getvalue()


def png(width: int = 3000, height: int = 2000) -> bytes:
    buf = io.BytesIO()
    picture(width, height).save(buf, "PNG")
    return buf.getvalue()


def zipped(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx() -> bytes:
    cell = lambda text: f"<w:tc><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>"  # noqa: E731
    return zipped({
        "[Content_Types].xml": "<Types/>",
        "word/document.xml": (
            f"<w:document {W}><w:body>"
            "<w:p><w:r><w:t>Objednávka č. 42</w:t></w:r><w:r><w:tab/><w:t>Brno</w:t></w:r></w:p>"
            f"<w:tbl><w:tr>{cell('Položka')}{cell('Cena')}</w:tr><w:tr>{cell('Výfuk')}{cell('1 990 Kč')}</w:tr></w:tbl>"
            "<w:p><w:r><w:t>Děkujeme.</w:t></w:r></w:p>"
            "</w:body></w:document>"
        ),
    })


def xlsx() -> bytes:
    main = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    return zipped({
        "[Content_Types].xml": "<Types/>",
        "xl/workbook.xml": f'<workbook {main} {rel}><sheets><sheet name="Faktury" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/sharedStrings.xml": f"<sst {main}><si><t>Číslo</t></si><si><t>Částka</t></si><si><t>FV-2026-001</t></si></sst>",
        "xl/worksheets/sheet1.xml": (
            f"<worksheet {main}><sheetData>"
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>12100.5</v></c><c r="D2" t="inlineStr"><is><t>zaplaceno</t></is></c></row>'
            "</sheetData></worksheet>"
        ),
    })


def odt() -> bytes:
    ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
          'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
          'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"')
    return zipped({
        "mimetype": "application/vnd.oasis.opendocument.text",
        "content.xml": (
            f"<office:document-content {ns}><office:body><office:text>"
            "<text:h>Nabídka</text:h><text:p>Cena<text:s text:c=\"2\"/>bez DPH</text:p>"
            "<table:table><table:table-row><table:table-cell><text:p>A</text:p></table:table-cell>"
            "<table:table-cell><text:p>B</text:p></table:table-cell></table:table-row></table:table>"
            "</office:text></office:body></office:document-content>"
        ),
    })


def mail(attachments: list[tuple[str, str, bytes, dict]]) -> bytes:
    msg = EmailMessage()
    msg["From"] = "sender@example.test"
    msg["To"] = "box@example.test"
    msg["Subject"] = "Podklady"
    msg["Date"] = "Thu, 19 Nov 2026 08:10:50 +0100"
    msg["Message-ID"] = "<m13@example.test>"
    msg.set_content("Dobrý den, posílám podklady.")
    for filename, mime, payload, extra in attachments:
        maintype, subtype = mime.split("/")
        if maintype == "text":
            msg.add_attachment(payload.decode(extra.get("charset", "utf-8")), subtype=subtype,
                               filename=filename, charset=extra.get("charset", "utf-8"))
        else:
            msg.add_attachment(payload, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


FILES = {
    "faktura.pdf": ("application/pdf", text_pdf(["Faktura FV-2026-001", "Celkem 12100 CZK"]), {}),
    "sken.pdf": ("application/pdf", scanned_pdf(), {}),
    "fotka.png": ("image/png", png(), {}),
    "objednavka.docx": ("application/octet-stream", docx(), {}),
    "prehled.xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", xlsx(), {}),
    "nabidka.odt": ("application/vnd.oasis.opendocument.text", odt(), {}),
    "data.csv": ("text/csv", "kód;název\n1;Žluťoučký kůň\n".encode("cp1250"), {"charset": "windows-1250"}),
    "stranka.html": ("text/html", b"<html><body><h1>Ahoj</h1><script>evil()</script><p>svete</p></body></html>", {}),
    "archiv.zip": ("application/zip", zipped({"a.txt": "x"}), {}),
    "rozbite.pdf": ("application/pdf", b"%PDF-1.4 not really a pdf", {}),
}


# ---------------------------------------------------------------- fake IMAP server


BODYSTRUCTURE = (
    '(("text" "plain" ("charset" "utf-8") NIL NIL "7bit" 30 1 NIL NIL NIL NIL)'
    '("application" "pdf" ("name" "faktura.pdf") NIL NIL "base64" 1234 NIL ("attachment" ("filename" "faktura.pdf")) NIL NIL)'
    '("image" "png" NIL NIL NIL "base64" 5678 NIL ("attachment" ("filename*" "utf-8\'\'%C4%8Desk%C3%A1%20fotka.png")) NIL NIL)'
    ' "mixed" ("boundary" "b1") NIL NIL NIL)'
)


class AttachmentServer(WireServer):
    """Holds messages and their flags; answers UID SEARCH, UID FETCH (bodies, sizes and a
    canned BODYSTRUCTURE) and UID STORE the way an IMAP server does (RFC 3501)."""

    def __init__(self, messages: dict[int, bytes], structures: dict[int, str] | None = None) -> None:
        super().__init__()
        self.messages = dict(messages)
        self.structures = structures or {}
        self.flags: dict[int, set[str]] = {uid: set() for uid in messages}
        self.read_write = False

    def _answer(self, command: Command) -> None:
        line = command.line.decode("ascii", "replace")
        if command.name in {"SELECT", "EXAMINE"}:
            self.read_write = command.name == "SELECT"
        elif command.name == "UID SEARCH":
            wanted = re.search(r"\bUID (\d+)", line.split("SEARCH", 1)[1])
            uids = [int(wanted.group(1))] if wanted else sorted(self.messages)
            uids = [u for u in uids if u in self.messages]
            self._out += ("* SEARCH " + " ".join(map(str, uids))).rstrip().encode() + b"\r\n"
            self._out += f"{command.tag} OK SEARCH completed\r\n".encode()
            return
        elif command.name == "UID FETCH":
            self._out += self._fetch(line)
            self._out += f"{command.tag} OK FETCH completed\r\n".encode()
            return
        super()._answer(command)

    def _fetch(self, line: str) -> bytes:
        uid_set, items = re.match(r"\S+ UID FETCH (\S+) (.*)$", line).groups()
        items = items.upper()
        out = bytearray()
        uids = [int(u) for u in uid_set.split(",") if u.isdigit() and int(u) in self.messages]
        for seq, uid in enumerate(uids, start=1):
            raw = self.messages[uid]
            body = re.search(r"BODY(\.PEEK)?\[(HEADER)?\]", items)
            if body and not body.group(1) and self.read_write:
                self.flags[uid].add(SEEN)
            meta = f"* {seq} FETCH (UID {uid} FLAGS ({' '.join(sorted(self.flags[uid]))})"
            if "RFC822.SIZE" in items:
                meta += f" RFC822.SIZE {len(raw)}"
            if "BODYSTRUCTURE" in items and uid in self.structures:
                meta += f" BODYSTRUCTURE {self.structures[uid]}"
            if body is None:
                out += (meta + ")\r\n").encode()
                continue
            if body.group(2):
                raw = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
            out += f"{meta} BODY[{body.group(2) or ''}] {{{len(raw)}}}\r\n".encode() + raw + b")\r\n"
        return bytes(out)


def make_settings(tmp_path, **extra) -> Settings:
    values = dict(
        MAILBOX_LABEL="box@example.test", BASE_URL="https://mcp.example.test",
        GITHUB_CLIENT_ID="x", GITHUB_CLIENT_SECRET="x",
        IMAP_HOST="imap.example.test", IMAP_USER="box@example.test", IMAP_PASS="x",
        SMTP_HOST="smtp.example.test", SMTP_USER="box@example.test", SMTP_PASS="x",
        ALLOWED_RECIPIENTS="@example.test", DATA_DIR=str(tmp_path),
    )
    values.update(extra)
    return Settings(**values)


ATTACHED = mail([(name, mime, payload, extra) for name, (mime, payload, extra) in FILES.items()])


@pytest.fixture
def server(monkeypatch) -> AttachmentServer:
    wire = AttachmentServer({7: ATTACHED, 8: mail([])}, {7: BODYSTRUCTURE})
    monkeypatch.setattr(imap_module, "MailBox", wire.mailbox_class())
    return wire


async def call(tmp_path, tool: str, arguments: dict, **settings_extra):
    mcp = FastMCP("test")
    tools.register(mcp, make_settings(tmp_path, **settings_extra))
    async with Client(mcp) as client:
        return await client.call_tool(tool, arguments)


async def attachment(tmp_path, filename: str, **arguments):
    result = await call(tmp_path, "mail_get_attachment", {"uid": "7", "filename": filename, **arguments})
    images = [block for block in result.content if isinstance(block, ImageContent)]
    return result.structured_content, images


def unframed(data: dict) -> str:
    text = data["text"]
    assert text.startswith("--- BEGIN UNTRUSTED ATTACHMENT TEXT ---\n"), text[:80]
    assert text.endswith("\n--- END UNTRUSTED ATTACHMENT TEXT ---"), text[-80:]
    return text.split("\n", 1)[1].rsplit("\n", 1)[0]


# ---------------------------------------------------------------- readable by default


async def test_a_text_pdf_comes_back_as_text_page_by_page(tmp_path, server):
    data, images = await attachment(tmp_path, "faktura.pdf")
    assert data["format"] == "readable" and data["kind"] == "pdf" and data["readable"] is True
    text = unframed(data)
    assert "[Page 1]" in text and "Faktura FV-2026-001" in text
    assert "[Page 2]" in text and "Celkem 12100 CZK" in text
    assert text.index("Faktura") < text.index("Celkem")
    assert data["pages"] == [{"page": 1, "has_text": True}, {"page": 2, "has_text": True}]
    assert images == []
    assert data["truncated"] is False and data["text_chars"] == len(text)
    assert "base64" not in data
    assert data["UNTRUSTED_CONTENT_NOTE"].startswith("The content below arrived by email")


async def test_a_scanned_pdf_page_says_so_and_comes_back_as_an_image(tmp_path, server):
    data, images = await attachment(tmp_path, "sken.pdf")
    assert data["readable"] is True
    assert data["pages"] == [{"page": 1, "has_text": False, "image": True}]
    assert "no text layer" in unframed(data)
    assert len(images) == 1 and images[0].mimeType in {"image/jpeg", "image/png"}
    seen = Image.open(io.BytesIO(base64.b64decode(images[0].data)))
    assert max(seen.size) <= 1568 and seen.size[0] < seen.size[1]  # still the portrait page


async def test_an_image_is_image_content_scaled_down(tmp_path, server):
    data, images = await attachment(tmp_path, "fotka.png")
    assert data["kind"] == "image" and data["readable"] is True and data["images"] == 1
    assert len(images) == 1
    seen = Image.open(io.BytesIO(base64.b64decode(images[0].data)))
    assert seen.size == (1568, 1045)  # 3000 x 2000, long edge 1568, aspect kept
    assert f"image/{seen.format.lower()}" == images[0].mimeType


async def test_a_docx_comes_back_as_text_with_table_rows(tmp_path, server):
    data, _ = await attachment(tmp_path, "objednavka.docx")
    assert data["kind"] == "docx"
    assert unframed(data) == "Objednávka č. 42\tBrno\nPoložka | Cena\nVýfuk | 1 990 Kč\n\nDěkujeme."


async def test_an_xlsx_comes_back_as_rows_per_sheet(tmp_path, server):
    data, _ = await attachment(tmp_path, "prehled.xlsx")
    assert data["kind"] == "xlsx"
    assert unframed(data) == "## Sheet: Faktury\nČíslo\tČástka\nFV-2026-001\t12100.5\t\tzaplaceno"


async def test_an_odt_comes_back_as_text(tmp_path, server):
    data, _ = await attachment(tmp_path, "nabidka.odt")
    assert unframed(data) == "Nabídka\nCena  bez DPH\nA | B"


async def test_text_formats_are_decoded_with_their_charset_and_html_is_stripped(tmp_path, server):
    csv, _ = await attachment(tmp_path, "data.csv")
    assert unframed(csv) == "kód;název\n1;Žluťoučký kůň"
    html, _ = await attachment(tmp_path, "stranka.html")
    assert unframed(html) == "Ahoj\nsvete"


async def test_an_unknown_or_broken_file_says_so_and_points_to_base64(tmp_path, server):
    for name in ("archiv.zip", "rozbite.pdf"):
        data, images = await attachment(tmp_path, name)
        assert data.get("status") != "refused", data
        assert data["filename"] == name and data["readable"] is False and images == []
        assert 'format="base64"' in data["detail"]
        assert "text" not in data


async def test_long_text_is_cut_only_with_truncated_and_the_total_length(tmp_path, server):
    data, _ = await attachment(tmp_path, "faktura.pdf")
    full = unframed(data)
    cut, _ = await call_cut(tmp_path)
    assert cut["truncated"] is True and cut["text_chars"] == len(full)
    assert unframed(cut).startswith(full[:20])
    assert len(unframed(cut)) < len(full)


async def call_cut(tmp_path):
    result = await call(tmp_path, "mail_get_attachment", {"uid": "7", "filename": "faktura.pdf"},
                        MAX_ATTACHMENT_TEXT_CHARS="20")
    return result.structured_content, None


async def test_base64_still_returns_the_original_bytes(tmp_path, server):
    for name, (mime, payload, _extra) in FILES.items():
        if mime.startswith("text/"):
            continue  # the mail library re-encodes text parts (line ends), so their bytes differ
        data, images = await attachment(tmp_path, name, format="base64")
        assert data["format"] == "base64" and images == []
        assert base64.b64decode(data["base64"]) == payload, name


async def test_reading_an_attachment_never_marks_the_mail_read(tmp_path, server):
    for name in FILES:
        await attachment(tmp_path, name)
    assert server.flags[7] == set()
    assert not server.mutations()
    fetches = [c.line.upper() for c in server.named("UID FETCH")]
    assert fetches and not any(b"BODY[" in f for f in fetches)


async def test_the_size_limit_still_applies(tmp_path, server):
    data, images = await attachment(tmp_path, "fotka.png")
    assert data["readable"]
    result = await call(tmp_path, "mail_get_attachment", {"uid": "7", "filename": "fotka.png"},
                        MAX_ATTACHMENT_BYTES="1000")
    assert result.structured_content["status"] == "refused"
    assert not [b for b in result.content if isinstance(b, ImageContent)]


def test_the_text_limit_defaults_to_200000(tmp_path):
    assert make_settings(tmp_path).max_attachment_text_chars == 200_000


# ---------------------------------------------------------------- lists know about attachments


@pytest.mark.parametrize(("tool", "arguments"), [
    ("mail_list", {}),
    ("mail_search", {"criteria": "ALL"}),
])
async def test_lists_report_real_attachments_and_size_without_reading_bodies(tmp_path, server, tool, arguments):
    result = await call(tmp_path, tool, arguments)
    by_uid = {m["uid"]: m for m in result.structured_content["messages"]}
    assert by_uid["7"]["has_attachments"] is True
    assert by_uid["7"]["attachments"] == ["faktura.pdf", "česká fotka.png"]
    assert by_uid["7"]["size_bytes"] == len(ATTACHED)
    assert by_uid["8"]["has_attachments"] is None  # the server gave no BODYSTRUCTURE: unknown, not "none"
    assert server.flags[7] == set() and server.flags[8] == set()
    assert not server.mutations()
    fetches = [c.line.upper() for c in server.named("UID FETCH")]
    assert any(b"BODYSTRUCTURE" in f for f in fetches)
    assert not any(b"BODY[]" in f or b"BODY.PEEK[]" in f for f in fetches), fetches


async def test_mail_get_reports_the_total_body_length_when_it_cuts(tmp_path, server):
    result = await call(tmp_path, "mail_get", {"uid": "7", "mark_seen": False}, MAX_BODY_CHARS="10")
    data = result.structured_content
    assert data["body_truncated"] is True and data["body_chars"] == len("Dobrý den, posílám podklady.")
    assert data["size_bytes"] == len(ATTACHED)
    assert data["has_attachments"] is True and len(data["attachments"]) == len(FILES)


# ---------------------------------------------------------------- BODYSTRUCTURE parsing


def test_bodystructure_with_literals_nesting_and_encoded_names():
    name = "příloha č1.pdf".encode()  # 8-bit, so the server sends it as a literal
    data = [
        (b'1 (UID 3 BODYSTRUCTURE (("text" "plain" NIL NIL NIL "7bit" 5 1 NIL NIL NIL NIL)'
         b'("application" "pdf" ("name" {%d}' % len(name), name),
        b') NIL NIL "base64" 10 NIL NIL NIL NIL) "mixed" NIL NIL NIL NIL))',
        b'2 (UID 4 BODYSTRUCTURE ("text" "plain" ("charset" "us-ascii") NIL NIL "7bit" 5 1 NIL NIL NIL NIL))',
        b'3 (UID 5 BODYSTRUCTURE (("text" "html" NIL NIL NIL "7bit" 5 1 NIL NIL NIL NIL)'
        b'("image" "png" NIL "<logo@x>" NIL "base64" 10 NIL ("inline" NIL) NIL NIL) "related" NIL NIL NIL NIL))',
        b'4 (UID 6 BODYSTRUCTURE (("text" "plain" NIL NIL NIL "7bit" 5 1 NIL NIL NIL NIL)'
        b'("message" "rfc822" NIL NIL NIL "7bit" 300 NIL '
        b'(("text" "plain" NIL NIL NIL "7bit" 5 1 NIL NIL NIL NIL)("application" "pdf" ("name" "=?UTF-8?B?xI1lc2vDoS5wZGY=?=") NIL NIL "base64" 10 NIL NIL NIL NIL) "mixed" NIL NIL NIL NIL) '
        b'12 NIL ("attachment" ("filename" "fwd.eml")) NIL NIL) "mixed" NIL NIL NIL NIL))',
    ]
    found = attachments_by_uid(data)
    assert found == {
        "3": ["příloha č1.pdf"],
        "4": [],
        "5": ["(no name, image/png)"],
        "6": ["fwd.eml", "česká.pdf"],
    }


def test_bodystructure_garbage_gives_nothing_rather_than_failing():
    assert attachments_by_uid([b'1 (UID 3 BODYSTRUCTURE ((("']) == {}
    assert attachments_by_uid([None, b""]) == {}


# ---------------------------------------------------------------- hostile files


def test_an_office_file_with_a_dtd_is_refused_not_expanded():
    from mail_mcp import attachments

    laughs = (
        '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        f"<w:document {W}><w:body><w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body></w:document>"
    )
    with pytest.raises(attachments.Unreadable, match="could not be read"):
        attachments.read("x.docx", "application/octet-stream", zipped({"word/document.xml": laughs}))


def test_an_office_file_that_unpacks_too_far_is_refused(monkeypatch):
    from mail_mcp import attachments

    monkeypatch.setattr(attachments, "MAX_UNZIPPED_BYTES", 1000)
    big = f"<w:document {W}><w:body><w:p><w:r><w:t>{'a' * 5000}</w:t></w:r></w:p></w:body></w:document>"
    with pytest.raises(attachments.Unreadable, match="unpacks to more"):
        attachments.read("x.docx", "application/octet-stream", zipped({"word/document.xml": big}))


@pytest.mark.parametrize("over", [1, 1_000_000], ids=["just over", "1 MB over"])
def test_an_office_file_whose_xml_unpacks_past_10_mb_is_refused(over):
    # Security review of 12c554f: the whole XML part is parsed in memory, so 50 MB of
    # small elements cost several hundred MB of RAM. The real cap, not a patched one.
    from mail_mcp import attachments

    head = f"<w:document {W}><w:body><w:p><w:r><w:t>"
    tail = "</w:t></w:r></w:p></w:body></w:document>"
    big = head + "a" * (10_000_000 + over - len(head) - len(tail)) + tail
    assert len(big) == 10_000_000 + over
    with pytest.raises(attachments.Unreadable, match="unpacks to more"):
        attachments.read("x.docx", "application/octet-stream", zipped({"word/document.xml": big}))


def test_an_office_file_whose_xml_is_just_under_10_mb_is_still_read():
    from mail_mcp import attachments

    head = f"<w:document {W}><w:body><w:p><w:r><w:t>"
    tail = "</w:t></w:r></w:p></w:body></w:document>"
    big = head + "a" * (10_000_000 - len(head) - len(tail)) + tail
    readable = attachments.read("x.docx", "application/octet-stream", zipped({"word/document.xml": big}))
    assert readable.text.startswith("aaaa")


def test_a_picture_with_too_many_pixels_is_refused_before_it_is_decoded(monkeypatch):
    from mail_mcp import attachments

    monkeypatch.setattr(attachments, "MAX_IMAGE_PIXELS", 1000)
    with pytest.raises(attachments.Unreadable, match="too big"):
        attachments.read("p.png", "image/png", png(100, 100))


def test_shared_strings_cannot_multiply_the_text_of_a_sheet():
    from mail_mcp import attachments

    main = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    row = "".join(f'<c t="s"><v>0</v></c>' for _ in range(1000))
    bomb = zipped({
        "xl/workbook.xml": f'<workbook {main} {rel}><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/sharedStrings.xml": f"<sst {main}><si><t>{'x' * 10_000}</t></si></sst>",
        "xl/worksheets/sheet1.xml": f"<worksheet {main}><sheetData>{'<row>' + row + '</row>' * 1}" + ("<row>" + row + "</row>") * 200 + "</sheetData></worksheet>",
    })
    found = attachments.read("b.xlsx", "application/octet-stream", bomb, max_chars=100)  # 2 billion characters unbounded
    assert found.complete is False and len(found.text) <= 1_000_000


def test_repeated_cells_and_rows_cannot_multiply_the_text_of_an_ods():
    from mail_mcp import attachments

    ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
          'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
          'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"')
    cell = f'<table:table-cell table:number-columns-repeated="1000"><text:p>{"y" * 10_000}</text:p></table:table-cell>'
    rows = f'<table:table-row table:number-rows-repeated="1000">{cell}</table:table-row>' * 50
    bomb = zipped({"content.xml": (
        f"<office:document-content {ns}><office:body><office:spreadsheet>"
        f'<table:table table:name="T">{rows}</table:table></office:spreadsheet></office:body></office:document-content>'
    )})
    found = attachments.read("b.ods", "application/octet-stream", bomb, max_chars=100)
    assert found.complete is False and len(found.text) <= 1_000_000


async def test_a_long_document_past_the_extraction_cap_says_its_length_is_not_exact(tmp_path, monkeypatch, server):
    from mail_mcp import attachments

    real = attachments.read
    monkeypatch.setattr(attachments, "read", lambda *a, **k: attachments.Readable("pdf", text="z" * 50, complete=False))
    result = await call(tmp_path, "mail_get_attachment", {"uid": "7", "filename": "faktura.pdf"}, MAX_ATTACHMENT_TEXT_CHARS="20")
    data = result.structured_content
    assert data["truncated"] is True and data["text_chars_exact"] is False
    assert "more than 50" in data["notes"][-1]
    monkeypatch.setattr(attachments, "read", real)


def test_a_scan_bigger_than_the_pixel_cap_is_not_decoded(monkeypatch):
    from mail_mcp import attachments

    monkeypatch.setattr(attachments, "MAX_IMAGE_PIXELS", 1000)
    decoded = []
    monkeypatch.setattr(attachments, "_picture", lambda img: decoded.append(img))
    found = attachments.read("s.pdf", "application/pdf", scanned_pdf())
    assert found.images == [] and decoded == []
    assert found.pages == [{"page": 1, "has_text": False, "image": False}]
    assert "1240 x 1754 pixels, too big" in found.notes[0]


# ---------------------------------------------------------------- security review 5976588156


ODF = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
       'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"')


def odf(kind: str, body: str) -> bytes:
    content = f"<office:spreadsheet>{body}</office:spreadsheet>" if kind == "ods" else f"<office:text>{body}</office:text>"
    return zipped({"content.xml": f"<office:document-content {ODF}><office:body>{content}</office:body></office:document-content>"})


@pytest.mark.parametrize("kind", ["ods", "odt"])
def test_the_reviewed_repeat_bomb_gives_cut_text_in_little_memory(kind):
    """The file from the review: 500 rows repeated 100 times, 10 cells repeated 100 times each.
    Unbounded it is 3.5 kB that builds more than 1 GB of text and 2 GB of memory."""
    import tracemalloc

    from mail_mcp import attachments

    cell = '<table:table-cell table:number-columns-repeated="100"><text:p>repeated cell text 1</text:p></table:table-cell>'
    rows = f'<table:table-row table:number-rows-repeated="100">{cell * 10}</table:table-row>' * 500
    bomb = odf(kind, f'<table:table table:name="T">{rows}</table:table>')
    assert len(bomb) < 4_000
    tracemalloc.start()
    try:
        found = attachments.read(f"b.{kind}", "application/octet-stream", bomb)  # MAX_ATTACHMENT_TEXT_CHARS default
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 100_000_000, f"{peak / 1e6:.0f} MB"
    assert found.complete is False and 0 < len(found.text) <= 2_000_000


@pytest.mark.parametrize("kind", ["ods", "odt"])
def test_repeated_spaces_are_not_built_past_the_cap(monkeypatch, kind):
    """text:s stands for up to 100 spaces: 3 million characters here, in one cell of thirty
    paragraphs (ODS) or in one paragraph (ODT)."""
    from mail_mcp import attachments

    if kind == "ods":
        paragraphs = ('<text:p>a' + '<text:s text:c="100"/>' * 1000 + 'b</text:p>') * 30
        body = f'<table:table table:name="T"><table:table-row><table:table-cell>{paragraphs}</table:table-cell></table:table-row></table:table>'
    else:
        body = '<text:p>a' + '<text:s text:c="100"/>' * 30_000 + 'b</text:p>'

    built: list[int] = []
    real = attachments._odf_text
    monkeypatch.setattr(attachments, "_odf_text", lambda *a, **k: built.append(len(text := real(*a, **k))) or text)
    found = attachments.read(f"s.{kind}", "application/octet-stream", odf(kind, body), max_chars=100)  # cap 1 million
    assert found.complete is False
    assert sum(built) <= 1_000_000 + 1_000, sum(built)


@pytest.mark.parametrize(("filename", "content_type", "payload"), [
    ("big.txt", "text/plain", b"a" * 1_500_000),
    ("big.html", "text/html", b"<p>" + b"b" * 1_500_000 + b"</p>"),
    ("big.eml", "message/rfc822", b"From: a@example.test\r\nSubject: x\r\n\r\n" + b"c" * 1_500_000),
])
def test_text_html_and_mail_attachments_stop_at_the_cap(filename, content_type, payload):
    from mail_mcp import attachments

    found = attachments.read(filename, content_type, payload, max_chars=100)  # cap 1 million
    assert found.complete is False and 0 < len(found.text) <= 1_000_000


def test_pages_past_the_picture_limit_are_not_decoded(monkeypatch):
    from mail_mcp import attachments

    buf = io.BytesIO()
    picture(200, 300).save(buf, "PDF", save_all=True, append_images=[picture(200, 300), picture(200, 300)])
    monkeypatch.setattr(attachments, "MAX_IMAGES", 1)
    looked = []
    real = attachments._largest_image
    monkeypatch.setattr(attachments, "_largest_image", lambda page: looked.append(page) or real(page))
    found = attachments.read("s.pdf", "application/pdf", buf.getvalue())
    assert len(found.images) == 1 and len(looked) == 1
    assert [p["image"] for p in found.pages] == [True, False, False]


def test_pillow_is_required_in_a_version_with_the_13_0_fixes():
    import pathlib
    import tomllib

    project = tomllib.loads((pathlib.Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]
    assert "pillow>=13,<14" in project["dependencies"]


# ---------------------------------------------------------------- QA review 5976717712


def test_pptx_slides_come_in_the_order_the_presentation_shows():
    """The second slide was dragged before the first: slide2.xml is shown first."""
    from mail_mcp import attachments

    p = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    slide = lambda text: f"<p:sld {p} {a}><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"  # noqa: E731
    deck = zipped({
        "ppt/presentation.xml": f'<p:presentation {p} {r}><p:sldIdLst><p:sldId id="257" r:id="rId8"/><p:sldId id="256" r:id="rId7"/></p:sldIdLst></p:presentation>',
        "ppt/_rels/presentation.xml.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId7" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>'
            '<Relationship Id="rId8" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="/ppt/slides/slide2.xml"/>'
            "</Relationships>"
        ),
        "ppt/slides/slide1.xml": slide("Original first"),
        "ppt/slides/slide2.xml": slide("Moved to first"),
    })
    found = attachments.read("deck.pptx", "application/octet-stream", deck)
    assert found.text == "## Slide 1\nMoved to first\n\n## Slide 2\nOriginal first"


@pytest.mark.parametrize(("rotate", "upright"), [(90, (200, 300)), (180, (300, 200)), (270, (200, 300)), (-90, (200, 300))])
def test_a_scan_on_a_rotated_page_comes_back_upright(rotate, upright):
    """A scanner stores the picture sideways and turns the page with /Rotate; the model must
    see the page as a PDF viewer shows it."""
    from pypdf import PdfReader, PdfWriter

    from mail_mcp import attachments

    sideways = Image.new("RGB", (300, 200), "white")
    sideways.paste((200, 30, 30), (0, 0, 30, 30))  # red corner: top left as stored
    buf = io.BytesIO()
    sideways.save(buf, "PDF")
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(buf.getvalue())))
    writer.pages[0].rotate(rotate)
    out = io.BytesIO()
    writer.write(out)
    found = attachments.read("scan.pdf", "application/pdf", out.getvalue())
    returned = Image.open(io.BytesIO(found.images[0][0]))
    assert returned.size == upright
    red = {90: (returned.width - 15, 15), 180: (returned.width - 15, returned.height - 15),
           270: (15, returned.height - 15), -90: (15, returned.height - 15)}[rotate]  # where a viewer shows the corner
    r, g, b = returned.convert("RGB").getpixel(red)
    assert r > 150 and g < 100 and b < 100, (r, g, b)
