from pathlib import Path
root=Path('/Users/pilarj/.dodaios/data/society/tester/workspace')
old=root/'evidence/mcp-mail-14-7266733-routine'
new=root/'evidence/mcp-mail-14-acb569b-routine'
script=(old/'check-independent.py').read_text()
script=script.replace("from mail_mcp import tools", "from mail_mcp import tools\nfrom imap_tools import MailMessage\nimport mail_mcp")
script=script.replace("checks=[]; results={}; wire=[]", "checks=[]; results={'imported_source':mail_mcp.__file__}; wire=[]\nFOLDER='QA-acb569b'")
script=script.replace("for data in [raw,empty.as_bytes()]:", "mb.create(FOLDER)\n    for data in [raw,empty.as_bytes()]:")
script=script.replace("mb.append('INBOX'", "mb.append(FOLDER")
script=script.replace("mb.select('INBOX'", "mb.select(FOLDER")
script=script.replace("client.call_tool(tool,args)", "client.call_tool(tool,{**args,'folder':FOLDER})")
script=script.replace("{'uid':uid,", "{'folder':FOLDER,'uid':uid,")
script=script.replace("len(('QA body '+('body-text '*30)).strip())", "len(MailMessage.from_bytes(raw).text.strip())")
script=script.replace("'7266733eb8047f78b0ab97a6532dab67f79f1873'", "'acb569bc9b182c3679f4deaf9300c507eb1e1dff'")
(new/'check-independent.py').write_text(script)
(new/'qa.crt').write_bytes((old/'qa.crt').read_bytes())
