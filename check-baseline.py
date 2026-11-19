import asyncio,base64,json,os
from pathlib import Path
from fastmcp import Client,FastMCP
from mail_mcp import tools
import mail_mcp
from mail_mcp.settings import Settings
OUT=Path(__file__).resolve().parent
os.environ['SSL_CERT_FILE']=str(OUT/'qa.crt')
s=Settings(MAILBOX_LABEL='qa@example.test',BASE_URL='https://qa.example.test',GITHUB_CLIENT_ID='unused',GITHUB_CLIENT_SECRET='unused',IMAP_HOST='localhost',IMAP_PORT=19914,IMAP_USER='qa@example.test',IMAP_PASS='unused-local-fixture',SMTP_HOST='localhost',SMTP_USER='qa@example.test',SMTP_PASS='unused-local-fixture',ALLOWED_RECIPIENTS='',MARK_SEEN_ON_GET=False,DATA_DIR=OUT/'state-base')
async def run():
    m=FastMCP('base QA'); tools.register(m,s)
    async with Client(m) as c:
        listed=(await c.call_tool('mail_list',{'folder':'QA-acb569b'})).structured_content
        uid=next(item['uid'] for item in listed['messages'] if item['subject']=='QA attachments 014')
        att=(await c.call_tool('mail_get_attachment',{'folder':'QA-acb569b','uid':uid,'filename':'invoice.pdf'})).structured_content
        expected=(OUT/'fixtures/invoice.pdf').read_bytes()
        result={'base':'bf8fe4d67c7bff846dec410a1e5f21d9091b701e','imported_source':mail_mcp.__file__,'list':listed,'attachment_keys':list(att),'base64_preserved':base64.b64decode(att['base64'])==expected,'readable_mode_present':'readable' in att}
        (OUT/'baseline-results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
        assert result['base64_preserved'] and not result['readable_mode_present']
asyncio.run(run())
