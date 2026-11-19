from __future__ import annotations
import asyncio, base64, hashlib, imaplib, io, json, os, ssl, sys, traceback
from pathlib import Path
from email.message import EmailMessage
from email import policy
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from openpyxl import Workbook, load_workbook
from docx import Document
from pptx import Presentation
from pypdf import PdfReader, PdfWriter
from fastmcp import FastMCP, Client
from mcp.types import ImageContent
from mail_mcp.settings import Settings
from mail_mcp import tools
from imap_tools import MailMessage
import mail_mcp

OUT=Path(__file__).resolve().parent
FIX=OUT/'fixtures'; FIX.mkdir(exist_ok=True)
os.environ['SSL_CERT_FILE']=str(OUT/'qa.crt')
checks=[]; results={'imported_source':mail_mcp.__file__}; wire=[]
FOLDER='QA-followup-12c554f'
def check(name, condition, observed=None):
    checks.append({'name':name,'pass':bool(condition),'observed':observed})
    print(('PASS ' if condition else 'FAIL ')+name, flush=True)
def save(name, payload, mime):
    (FIX/name).write_bytes(payload)
    return name,mime,payload
files=[]
buf=io.BytesIO(); c=canvas.Canvas(buf,pagesize=(600,800)); c.setFont('Helvetica',18)
for y,t in [(740,'QA INVOICE FV-2026-014'),(700,'Line A: 100.00 CZK'),(660,'Line B: 200.00 CZK'),(600,'Total: 300.00 CZK')]: c.drawString(45,y,t)
c.showPage(); c.save(); files.append(save('invoice.pdf',buf.getvalue(),'application/pdf'))
img=Image.new('RGB',(900,600),'white'); d=ImageDraw.Draw(img)
d.rectangle((0,0,449,599), fill='red'); d.rectangle((450,0,899,599), fill='blue'); d.text((30,30),'QA PHONE ORIENTATION',fill='white',font_size=34)
exif=Image.Exif(); exif[274]=6; buf=io.BytesIO(); img.save(buf,'JPEG',exif=exif,quality=95)
files.append(save('phone-exif6.jpg',buf.getvalue(),'image/jpeg'))
scan=Image.new('RGB',(600,900),'white'); d=ImageDraw.Draw(scan); d.text((40,70),'QA SCAN 014',fill='black',font_size=48); d.text((40,170),'TOTAL 300 CZK',fill='black',font_size=40)
buf=io.BytesIO(); scan.save(buf,'PDF',resolution=72); files.append(save('scan.pdf',buf.getvalue(),'application/pdf'))
# A normal PDF page-rotation flag is common in scanned documents.
writer=PdfWriter(); writer.add_page(PdfReader(io.BytesIO(buf.getvalue())).pages[0]); writer.pages[0].rotate(90)
rot=io.BytesIO(); writer.write(rot); files.append(save('scan-rotate90.pdf',rot.getvalue(),'application/pdf'))
wb=Workbook(); ws=wb.active; ws.title='Invoices'; ws.append(['Number','Amount','Status']); ws.append(['FV-2026-014',300,'paid']); ws2=wb.create_sheet('Contacts'); ws2.append(['City','Brno'])
buf=io.BytesIO(); wb.save(buf); files.append(save('invoices.xlsx',buf.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))
doc=Document(); doc.add_paragraph('QA purchase order 014'); table=doc.add_table(rows=1,cols=2); table.cell(0,0).text='Item A'; table.cell(0,1).text='300 CZK'; buf=io.BytesIO(); doc.save(buf)
files.append(save('order.docx',buf.getvalue(),'application/vnd.openxmlformats-officedocument.wordprocessingml.document'))
prs=Presentation()
for title in ['Original first','Moved to first']:
    slide=prs.slides.add_slide(prs.slide_layouts[0]); slide.shapes.title.text=title
sld=prs.slides._sldIdLst; moved=sld[1]; sld.remove(moved); sld.insert(0,moved)
buf=io.BytesIO(); prs.save(buf); files.append(save('reordered.pptx',buf.getvalue(),'application/vnd.openxmlformats-officedocument.presentationml.presentation'))
reopened=Presentation(io.BytesIO(buf.getvalue())); results['pptx_expected_titles']=[s.shapes.title.text for s in reopened.slides]
inner=EmailMessage(policy=policy.SMTP); inner['From']='sender@example.test'; inner['To']='qa@example.test'; inner['Subject']='QA attached message'; inner.set_content('QA inner body 014'); inner.add_attachment(b'inner file 014',maintype='application',subtype='octet-stream',filename='inner.txt')
files.append(save('forwarded.eml',inner.as_bytes(),'message/rfc822'))
files.append(save('česká příloha.txt','Žluťoučký kůň 014'.encode(),'text/plain'))
msg=EmailMessage(policy=policy.SMTP); msg['From']='sender@example.test'; msg['To']='qa@example.test'; msg['Subject']='QA attachments 014'; msg['Message-ID']='<qa014@example.test>'; msg['Date']='Thu, 19 Nov 2026 15:00:00 +0100'; msg.set_content('QA body '+('body-text '*30))
for name,mime,payload in files:
    a,b=mime.split('/',1)
    if mime=='message/rfc822':
        msg.add_attachment(inner,filename=name)
    else: msg.add_attachment(payload,maintype=a,subtype=b,filename=name)
raw=msg.as_bytes(); (FIX/'attachments.eml').write_bytes(raw)
empty=EmailMessage(policy=policy.SMTP); empty['From']='sender@example.test'; empty['To']='qa@example.test'; empty['Subject']='QA no attachments'; empty['Message-ID']='<qa-empty014@example.test>'; empty['Date']=msg['Date']; empty.set_content('No attachments here.')

def connect():
    mb=imaplib.IMAP4_SSL('localhost',19924,ssl_context=ssl.create_default_context()); mb.login('qa@example.test','unused-local-fixture'); return mb
with connect() as mb:
    mb.create(FOLDER)
    for data in [raw,empty.as_bytes()]:
        typ,ans=mb.append(FOLDER,None,None,data); assert typ=='OK',ans
    mb.select(FOLDER,readonly=True); uids=mb.uid('SEARCH',None,'ALL')[1][0].split(); uid=uids[0].decode(); emptyuid=uids[1].decode()
    typ,metadata=mb.uid('FETCH',uid,'(FLAGS RFC822.SIZE BODYSTRUCTURE)'); results['initial_metadata']=[str(x) for x in metadata]

original_command=imaplib.IMAP4._command
def recording(self,name,*args):
    if name.upper()=='UID':
        wire.append({'command':name,'arguments':[str(x) for x in args]})
    return original_command(self,name,*args)
imaplib.IMAP4._command=recording
settings=Settings(MAILBOX_LABEL='qa@example.test',BASE_URL='https://qa.example.test',GITHUB_CLIENT_ID='unused',GITHUB_CLIENT_SECRET='unused',IMAP_HOST='localhost',IMAP_PORT=19924,IMAP_USER='qa@example.test',IMAP_PASS='unused-local-fixture',SMTP_HOST='localhost',SMTP_USER='qa@example.test',SMTP_PASS='unused-local-fixture',ALLOWED_RECIPIENTS='',MARK_SEEN_ON_GET=False,MAX_BODY_CHARS=30,DATA_DIR=OUT/'state')
async def main():
    mcp=FastMCP('independent QA'); tools.register(mcp,settings)
    async with Client(mcp) as client:
        for tool,args in [('mail_list',{}),('mail_search',{'criteria':'ALL'}),('mail_get_thread',{'message_id':'<qa014@example.test>'})]:
            start=len(wire); reply=await client.call_tool(tool,{**args,'folder':FOLDER}); data=reply.structured_content; results[tool]=data
            item=next(m for m in data['messages'] if m['uid']==uid)
            check(tool+' attachment status',item['has_attachments'] is True,item)
            expected=[n for n,_,_ in files]+['inner.txt']
            check(tool+' exact attachment names',sorted(item['attachments'])==sorted(expected),item['attachments'])
            check(tool+' true RFC822 size',item['size_bytes']==len(raw),{'actual':item['size_bytes'],'expected':len(raw)})
            commands=wire[start:]; fetches=[x for x in commands if x['arguments'][0].upper()=='FETCH']
            check(tool+' headers only and BODYSTRUCTURE',any('BODYSTRUCTURE' in str(x) for x in fetches) and not any('BODY[]' in str(x) or 'BODY.PEEK[]' in str(x) for x in fetches),commands)
        no=next(m for m in results['mail_list']['messages'] if m['uid']==emptyuid)
        check('mail without attachments is false',no['has_attachments'] is False and no['attachments']==[],no)
        full=(await client.call_tool('mail_get',{'folder':FOLDER,'uid':uid,'mark_seen':False})).structured_content; results['mail_get']=full
        check('body truncation reports full length',full['body_truncated'] and full['body_chars']==len(MailMessage.from_bytes(raw).text.strip()),full.get('body_chars'))
        check('mail_get original message size',full['size_bytes']==len(raw),full['size_bytes'])
        for name,mime,payload in files:
            reply=await client.call_tool('mail_get_attachment',{'folder':FOLDER,'uid':uid,'filename':name}); data=reply.structured_content; results[name]=data
            images=[b for b in reply.content if isinstance(b,ImageContent)]
            check(name+' readable default',data.get('readable') is True,data)
            if name=='invoice.pdf': check('invoice total and identifier preserved','300.00 CZK' in data.get('text','') and 'FV-2026-014' in data.get('text',''))
            if name=='invoices.xlsx': check('real XLSX sheets and values preserved','## Sheet: Contacts' in data.get('text','') and 'FV-2026-014\t300\tpaid' in data.get('text',''),data.get('text'))
            if name=='order.docx': check('real DOCX paragraph and table preserved','Item A | 300 CZK' in data.get('text',''))
            if name=='forwarded.eml': check('attached EML body and nested filename preserved','QA inner body 014' in data.get('text','') and 'inner.txt' in data.get('text',''),data.get('text'))
            if name=='reordered.pptx':
                text=data.get('text',''); expected=results['pptx_expected_titles']; check('PPTX presentation order preserved',all(t in text for t in expected) and text.index(expected[0])<text.index(expected[1]),text)
            for i,block in enumerate(images):
                imagebytes=base64.b64decode(block.data); (OUT/(name+f'.returned-{i}.jpg')).write_bytes(imagebytes); pic=Image.open(io.BytesIO(imagebytes)); results[name]['returned_size']=pic.size
                if name=='phone-exif6.jpg':
                    top=pic.getpixel((300,150)); bottom=pic.getpixel((300,750)); check('EXIF 6 phone image upright',pic.size==(600,900) and top[0]>200 and bottom[2]>200,{'size':pic.size,'top':top,'bottom':bottom})
                if name=='scan.pdf': check('scan portrait preserved',pic.size[0]<pic.size[1],pic.size)
                if name=='scan-rotate90.pdf': check('scanned PDF Rotate 90 respected',pic.size[0]>pic.size[1],{'returned':pic.size,'rotation':90})
            encoded=(await client.call_tool('mail_get_attachment',{'folder':FOLDER,'uid':uid,'filename':name,'format':'base64'})).structured_content
            if name!='forwarded.eml': check(name+' raw bytes unchanged',base64.b64decode(encoded['base64'])==payload)
    with connect() as mb:
        mb.select(FOLDER,readonly=True); flags=mb.uid('FETCH',','.join([uid,emptyuid]),'(FLAGS)')[1]; results['final_flags']=[str(x) for x in flags]
        check('all reads preserve unread flags',not any(b'\\Seen' in item for item in flags if isinstance(item,bytes)),results['final_flags'])
    check('no STORE mutation during reads',not any(x['arguments'][0].upper()=='STORE' for x in wire))
try: asyncio.run(main())
except Exception:
    results['harness_error']=traceback.format_exc(); print(results['harness_error']); raise
finally:
    (OUT/'independent-results.json').write_text(json.dumps({'head':'12c554f5abe89e30980d5399e1e28329ee3942de','checks':checks,'results':results,'wire':wire},ensure_ascii=False,indent=2))
print(f'TOTAL: {sum(c["pass"] for c in checks)}/{len(checks)} passed',flush=True)
sys.exit(1 if any(not c['pass'] for c in checks) else 0)
