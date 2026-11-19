from __future__ import annotations
import asyncio
import base64
import hashlib
import imaplib
import io
import json
import os
import ssl
import sys
import zipfile
from pathlib import Path
from email.message import EmailMessage
from email import policy
from xml.sax.saxutils import escape
from fastmcp import FastMCP, Client
from mcp.types import ImageContent
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import reportlab
from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from mail_mcp.settings import Settings
from mail_mcp import tools
import mail_mcp

OUT = Path(__file__).resolve().parent
REVISION = sys.argv[1]
FIX = OUT / 'format-fixtures'
FIX.mkdir(exist_ok=True)
os.environ['SSL_CERT_FILE'] = str(OUT / 'qa.crt')
TEXT = 'Žluťoučký kůň, příliš žluťoučký příběh. Číslo 14, částka 300 Kč.'
files = []
checks = []
responses = {}

def check(name, condition, observed=None):
    checks.append({'name': name, 'pass': bool(condition), 'observed': observed})
    print(('PASS ' if condition else 'FAIL ') + name, flush=True)

def add(name, mime, payload, expected=TEXT):
    (FIX / name).write_bytes(payload)
    files.append((name, mime, payload, expected))

pdfmetrics.registerFont(TTFont('QAVera', str(Path(reportlab.__file__).parent / 'fonts/Vera.ttf')))
buf = io.BytesIO()
pdf = canvas.Canvas(buf)
for number in range(1, 4):
    pdf.setFont('QAVera', 13)
    pdf.drawString(35, 740, f'Strana {number}: {TEXT}')
    pdf.showPage()
pdf.save()
add('české-stránky.pdf', 'application/pdf', buf.getvalue())
doc = Document()
doc.add_paragraph(TEXT)
table = doc.add_table(rows=1, cols=2)
table.cell(0, 0).text = 'Položka'
table.cell(0, 1).text = 'Částka 300 Kč'
buf = io.BytesIO()
doc.save(buf)
add('český-word.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', buf.getvalue())
wb = Workbook()
wb.active.title = 'Přílohy'
wb.active.append([TEXT, 300])
buf = io.BytesIO()
wb.save(buf)
add('český-excel.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', buf.getvalue())
prs = Presentation()
for title in [TEXT, 'Druhá stránka']:
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = title
buf = io.BytesIO()
prs.save(buf)
add('český-powerpoint.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation', buf.getvalue())
ns = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
      'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
      'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"')
for extension, mime in [('odt', 'application/vnd.oasis.opendocument.text'), ('ods', 'application/vnd.oasis.opendocument.spreadsheet')]:
    body = f'<text:p>{escape(TEXT)}</text:p>' if extension == 'odt' else f'<table:table table:name="Přílohy"><table:table-row><table:table-cell office:value-type="string"><text:p>{escape(TEXT)}</text:p></table:table-cell></table:table-row></table:table>'
    tag = 'text' if extension == 'odt' else 'spreadsheet'
    content = f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {ns} office:version="1.3"><office:body><office:{tag}>{body}</office:{tag}></office:body></office:document-content>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as archive:
        archive.writestr('mimetype', mime, compress_type=zipfile.ZIP_STORED)
        archive.writestr('content.xml', content, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr('META-INF/manifest.xml', f'<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"><manifest:file-entry manifest:full-path="/" manifest:media-type="{mime}"/><manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>')
    add('český-dokument.' + extension, mime, buf.getvalue())
add('česká-data.csv', 'text/csv', ('Název;Cena\n' + TEXT + ';300\n').encode())
add('windows-1250.csv', 'text/csv', TEXT.encode('cp1250'))
add('česká-stránka.html', 'text/html', f'<html><body><p>{TEXT}</p><script>DO_NOT_INCLUDE_QA_SCRIPT</script></body></html>'.encode())
inner = EmailMessage(policy=policy.SMTP)
inner['From'] = 'qa-sender@example.test'
inner['To'] = 'qa@example.test'
inner['Subject'] = 'Česká příloha'
inner.set_content(TEXT)
add('český-mail.eml', 'application/octet-stream', inner.as_bytes())
add('česká-faktura.isdoc', 'application/xml', f'<?xml version="1.0" encoding="UTF-8"?><Invoice xmlns="http://isdoc.cz/namespace/2013"><Note>{TEXT}</Note><PayableAmount>300</PayableAmount></Invoice>'.encode())
add('české-xml.xml', 'application/xml', f'<root>{TEXT}</root>'.encode())
add('české-json.json', 'application/json', json.dumps({'text': TEXT}, ensure_ascii=False).encode())
add('český-text.txt', 'text/plain', TEXT.encode())
image = Image.new('RGB', (600, 900), 'white')
buf = io.BytesIO()
image.save(buf, 'PNG')
add('obrázek.png', 'image/png', buf.getvalue(), None)
buf = io.BytesIO()
image.save(buf, 'PDF')
add('sken.pdf', 'application/pdf', buf.getvalue(), None)
LONG = TEXT * 5000
add('dlouhý-text.txt', 'text/plain', LONG.encode(), TEXT)
add('poškozené.pdf', 'application/pdf', b'%PDF-1.7 broken QA file', False)
add('nepodporovaný.zip', 'application/zip', b'QA unsupported archive', False)

message = EmailMessage(policy=policy.SMTP)
message['From'] = 'qa-sender@example.test'
message['To'] = 'qa@example.test'
message['Subject'] = 'QA all attachment formats ' + REVISION
message.set_content('Celý vlastní testovací mail. ' + TEXT)
for name, mime, payload, expected in files:
    major, minor = mime.split('/', 1)
    message.add_attachment(payload, maintype=major, subtype=minor, filename=name)
raw = message.as_bytes()
folder = 'QA-formats-' + REVISION
with imaplib.IMAP4_SSL('localhost', 19924, ssl_context=ssl.create_default_context()) as mailbox:
    mailbox.login('qa@example.test', 'unused-local-fixture')
    mailbox.create(folder)
    status, info = mailbox.append(folder, None, None, raw)
    assert status == 'OK', info
    mailbox.select(folder, readonly=True)
    uid = mailbox.uid('SEARCH', None, 'ALL')[1][0].split()[-1].decode()

base_settings = dict(MAILBOX_LABEL='qa@example.test', BASE_URL='https://qa.example.test', GITHUB_CLIENT_ID='unused', GITHUB_CLIENT_SECRET='unused', IMAP_HOST='localhost', IMAP_PORT=19924, IMAP_USER='qa@example.test', IMAP_PASS='unused-local-fixture', SMTP_HOST='localhost', SMTP_USER='qa@example.test', SMTP_PASS='unused-local-fixture', ALLOWED_RECIPIENTS='', DATA_DIR=OUT / ('state-' + REVISION), MARK_SEEN_ON_GET=False)

def extracted(data):
    prefix = '--- BEGIN UNTRUSTED ATTACHMENT TEXT ---\n'
    suffix = '\n--- END UNTRUSTED ATTACHMENT TEXT ---'
    text = data.get('text', '')
    assert text.startswith(prefix) and text.endswith(suffix)
    return text[len(prefix):-len(suffix)]

async def run():
    server = FastMCP('QA complete attachment formats')
    tools.register(server, Settings(**base_settings))
    async with Client(server) as client:
        for tool, args in [('mail_list', {}), ('mail_search', {'criteria': 'ALL'})]:
            reply = (await client.call_tool(tool, {'folder': folder, **args})).structured_content
            entry = next(m for m in reply['messages'] if m['uid'] == uid)
            check(tool + ' true status, exact names and MIME size', entry['has_attachments'] is True and sorted(entry['attachments']) == sorted(f[0] for f in files) and entry['size_bytes'] == len(raw), {'size': entry['size_bytes'], 'expected_size': len(raw), 'names': entry['attachments']})
        for name, mime, payload, expected in files:
            reply = await client.call_tool('mail_get_attachment', {'folder': folder, 'uid': uid, 'filename': name})
            data = reply.structured_content
            responses[name] = data
            if expected is False:
                check(name + ' readable error', data.get('readable') is False and 'base64' in data.get('detail', '') and ('damaged' in data.get('detail', '') or 'not a format' in data.get('detail', '')), data)
                # Exercise the same MCP session immediately after each parser error.
                recovery = (await client.call_tool('mail_get_attachment', {'folder': folder, 'uid': uid, 'filename': 'český-text.txt'})).structured_content
                check(name + ' server continues', recovery.get('readable') is True and TEXT in recovery.get('text', ''))
            elif expected is None:
                check(name + ' returned as MCP image', data.get('readable') is True and any(isinstance(block, ImageContent) for block in reply.content), {'images': data.get('images')})
            else:
                check(name + ' Czech text readable', data.get('readable') is True and expected in data.get('text', ''), {'text_chars': data.get('text_chars')})
                if name.endswith('.pdf'):
                    text = extracted(data)
                    check('three Czech PDF pages in order', all(f'Strana {n}:' in text for n in [1, 2, 3]) and text.index('Strana 1:') < text.index('Strana 2:') < text.index('Strana 3:'), data.get('pages'))
                if name.endswith('.html'):
                    check('HTML script excluded', 'DO_NOT_INCLUDE_QA_SCRIPT' not in data.get('text', ''))
                if name == 'dlouhý-text.txt':
                    check('default 200000 character cap and notice', data.get('truncated') is True and len(extracted(data)) == 200000 and data.get('text_chars') == len(LONG) and any('MAX_ATTACHMENT_TEXT_CHARS' in note for note in data.get('notes', [])), {'returned': len(extracted(data)), 'total': data.get('text_chars'), 'notes': data.get('notes')})
            encoded = (await client.call_tool('mail_get_attachment', {'folder': folder, 'uid': uid, 'filename': name, 'format': 'base64'})).structured_content
            returned = base64.b64decode(encoded['base64'])
            check(name + ' original bytes preserved', returned == payload, {'expected_sha256': hashlib.sha256(payload).hexdigest(), 'actual_sha256': hashlib.sha256(returned).hexdigest()})
        full = (await client.call_tool('mail_get', {'folder': folder, 'uid': uid, 'mark_seen': False})).structured_content
        check('whole Czech mail body readable', TEXT in full.get('body', ''))
    custom = FastMCP('QA configurable attachment cap')
    tools.register(custom, Settings(**base_settings, MAX_ATTACHMENT_TEXT_CHARS=97))
    async with Client(custom) as client:
        data = (await client.call_tool('mail_get_attachment', {'folder': folder, 'uid': uid, 'filename': 'dlouhý-text.txt'})).structured_content
        check('configured 97 character cap and notice', len(extracted(data)) == 97 and data.get('truncated') is True and any('97' in note for note in data.get('notes', [])), {'returned': len(extracted(data)), 'notes': data.get('notes')})
    with imaplib.IMAP4_SSL('localhost', 19924, ssl_context=ssl.create_default_context()) as mailbox:
        mailbox.login('qa@example.test', 'unused-local-fixture')
        mailbox.select(folder, readonly=True)
        flags = mailbox.uid('FETCH', uid, '(FLAGS)')[1]
        check('message remains unread', not any(b'\\Seen' in part for part in flags if isinstance(part, bytes)), [str(item) for item in flags])

try:
    asyncio.run(run())
finally:
    report = {'revision': REVISION, 'source': mail_mcp.__file__, 'checks': checks, 'responses': responses, 'message_bytes': len(raw), 'attachment_count': len(files)}
    (OUT / ('formats-' + REVISION + '.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(f'TOTAL {sum(item["pass"] for item in checks)}/{len(checks)}', flush=True)
raise SystemExit(1 if any(not item['pass'] for item in checks) else 0)
