import asyncio, base64, imaplib, io, json, os, ssl
from pathlib import Path
from email.message import EmailMessage
from email import policy
from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
import pymupdf
from fastmcp import FastMCP, Client
from mcp.types import ImageContent
from mail_mcp import tools
from mail_mcp.settings import Settings
OUT=Path(__file__).resolve().parent
os.environ['SSL_CERT_FILE']=str(OUT/'qa.crt')
# The scanner records sideways pixels and the PDF page rotates them upright.
upright=Image.new('RGB',(600,900),'white'); draw=ImageDraw.Draw(upright)
draw.rectangle((0,0,599,34),fill='red'); draw.text((30,80),'QA SCAN 014',fill='black',font_size=48); draw.text((30,180),'TOTAL 300 CZK',fill='black',font_size=40)
sideways=upright.transpose(Image.Transpose.ROTATE_90)
buf=io.BytesIO(); sideways.save(buf,'PDF',resolution=72)
writer=PdfWriter(); writer.add_page(PdfReader(io.BytesIO(buf.getvalue())).pages[0]); writer.pages[0].rotate(90)
buf=io.BytesIO(); writer.write(buf); pdf=buf.getvalue(); (OUT/'fixtures'/'scan-upright-with-rotation.pdf').write_bytes(pdf)
rendered=pymupdf.open(stream=pdf,filetype='pdf'); page=rendered[0]; pix=page.get_pixmap(); pix.save(str(OUT/'scan-expected-render.png'))
expected_size=(pix.width,pix.height)
msg=EmailMessage(policy=policy.SMTP); msg['From']='sender@example.test'; msg['To']='qa@example.test'; msg['Subject']='QA rotated scan'; msg.set_content('QA rotated scan fixture'); msg.add_attachment(pdf,maintype='application',subtype='pdf',filename='scan-upright-with-rotation.pdf')
with imaplib.IMAP4_SSL('localhost',19914,ssl_context=ssl.create_default_context()) as mb:
    mb.login('qa@example.test','unused-local-fixture'); folder='QA-rotation-acb569b'; mb.create(folder); mb.append(folder,None,None,msg.as_bytes()); mb.select(folder,readonly=True); uid=mb.uid('SEARCH',None,'ALL')[1][0].split()[-1].decode()
settings=Settings(MAILBOX_LABEL='qa@example.test',BASE_URL='https://qa.example.test',GITHUB_CLIENT_ID='unused',GITHUB_CLIENT_SECRET='unused',IMAP_HOST='localhost',IMAP_PORT=19914,IMAP_USER='qa@example.test',IMAP_PASS='unused-local-fixture',SMTP_HOST='localhost',SMTP_USER='qa@example.test',SMTP_PASS='unused-local-fixture',ALLOWED_RECIPIENTS='',DATA_DIR=OUT/'state')
async def run():
    mcp=FastMCP('scan QA'); tools.register(mcp,settings)
    async with Client(mcp) as client:
        reply=await client.call_tool('mail_get_attachment',{'uid':uid,'folder':folder,'filename':'scan-upright-with-rotation.pdf'})
        block=next(b for b in reply.content if isinstance(b,ImageContent)); raw=base64.b64decode(block.data); actual=Image.open(io.BytesIO(raw)); actual.save(OUT/'scan-actual-returned.png')
        output={'head':'acb569bc9b182c3679f4deaf9300c507eb1e1dff','expected_size':expected_size,'actual_size':actual.size,'reply':reply.structured_content,'correct_orientation':actual.size==expected_size}
        (OUT/'scan-rotation-results.json').write_text(json.dumps(output,indent=2)); print(json.dumps(output)); return output['correct_orientation']
ok=asyncio.run(run())
raise SystemExit(0 if ok else 1)
