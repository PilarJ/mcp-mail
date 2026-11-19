import asyncio
import base64
import hashlib
import imaplib
import json
import os
import ssl
import sys
from pathlib import Path
from email.message import EmailMessage
from email import policy
import pymupdf
from fastmcp import FastMCP, Client
from mail_mcp import tools
from mail_mcp.settings import Settings
import mail_mcp

out = Path(__file__).resolve().parent
revision = sys.argv[1]
os.environ['SSL_CERT_FILE'] = str(out / 'qa.crt')
text = 'Žluťoučký kůň, příliš žluťoučký příběh. Číslo 14, částka 300 Kč.'
font = pymupdf.Font(fontfile=str(out / 'qa-unicode-font.ttf'))
assert all(font.has_glyph(ord(char)) for char in text)
document = pymupdf.open()
for number in range(1, 4):
    page = document.new_page()
    page.insert_font(fontname='qa', fontfile=str(out / 'qa-unicode-font.ttf'))
    page.insert_text((30, 80), f'Strana {number}: {text}', fontname='qa', fontsize=12)
document.subset_fonts()
payload = document.tobytes(garbage=4, deflate=True)
(out / 'czech-valid-font.pdf').write_bytes(payload)
independent = pymupdf.open(stream=payload, filetype='pdf')
independent_text = '\n'.join(page.get_text() for page in independent)
assert text in independent_text
independent[0].get_pixmap().save(str(out / 'czech-valid-font-page1.png'))
message = EmailMessage(policy=policy.SMTP)
message['From'] = 'qa-sender@example.test'
message['To'] = 'qa@example.test'
message['Subject'] = 'QA corrected Czech PDF ' + revision
message.set_content('Synthetic Czech PDF fixture')
message.add_attachment(payload, maintype='application', subtype='pdf', filename='české-stránky-opravené.pdf')
folder = 'QA-czech-pdf-' + revision
with imaplib.IMAP4_SSL('localhost', 19924, ssl_context=ssl.create_default_context()) as mailbox:
    mailbox.login('qa@example.test', 'unused-local-fixture')
    mailbox.create(folder)
    assert mailbox.append(folder, None, None, message.as_bytes())[0] == 'OK'
    mailbox.select(folder, readonly=True)
    uid = mailbox.uid('SEARCH', None, 'ALL')[1][0].split()[-1].decode()
settings = Settings(MAILBOX_LABEL='qa@example.test', BASE_URL='https://qa.example.test', GITHUB_CLIENT_ID='unused', GITHUB_CLIENT_SECRET='unused', IMAP_HOST='localhost', IMAP_PORT=19924, IMAP_USER='qa@example.test', IMAP_PASS='unused-local-fixture', SMTP_HOST='localhost', SMTP_USER='qa@example.test', SMTP_PASS='unused-local-fixture', ALLOWED_RECIPIENTS='', DATA_DIR=out / ('state-czech-' + revision))
async def run():
    mcp = FastMCP('Czech PDF QA')
    tools.register(mcp, settings)
    async with Client(mcp) as client:
        args = {'folder': folder, 'uid': uid, 'filename': 'české-stránky-opravené.pdf'}
        result = (await client.call_tool('mail_get_attachment', args)).structured_content
        actual = result['text']
        raw = (await client.call_tool('mail_get_attachment', {**args, 'format': 'base64'})).structured_content
        checks = [
            {'name': 'independent PDF writer/reader preserves all Czech glyphs', 'pass': text in independent_text},
            {'name': 'actual MCP preserves Czech text on all three PDF pages', 'pass': actual.count(text) == 3},
            {'name': 'all three PDF page labels and source order retained', 'pass': all(f'Strana {number}:' in actual for number in [1, 2, 3]) and actual.index('Strana 1:') < actual.index('Strana 2:') < actual.index('Strana 3:') and len(result['pages']) == 3},
            {'name': 'Czech PDF base64 is byte-identical', 'pass': base64.b64decode(raw['base64']) == payload}
        ]
        data = {'revision': revision, 'source': mail_mcp.__file__, 'checks': checks, 'response': result, 'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload), 'independent_text': independent_text}
        (out / ('czech-pdf-' + revision + '.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(json.dumps({'revision': revision, 'checks': checks, 'bytes': len(payload)}, ensure_ascii=False))
        assert all(item['pass'] for item in checks)
asyncio.run(run())
