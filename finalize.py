from pathlib import Path
import json, shutil, hashlib
root=Path('/Users/pilarj/.dodaios/data/society/tester/workspace')
e=root/'evidence/mcp-mail-14-acb569b-routine'; old=root/'evidence/mcp-mail-14-7266733-routine'
for name in ['Dockerfile.dovecot','dovecot.conf','dovecot-build.log','dovecot-build.exit','setup.log','setup.exit']:
    shutil.copy2(old/name,e/name)
for name in ['suite.log','suite.exit','independent.log','independent-results.json']:
    shutil.copy2(old/name,e/('initial-'+name))
lessons=root/'POUCENI.md'; lines=lessons.read_text().splitlines()
lesson='2026-11-19 – mcp-mail PR 14 QA initially assumed body_chars excluded text attachments, and an optional missing-file read hid a successful batch – Derive expected body length from the full MIME parser, guard optional reads individually, and pin/retest an advanced PR head before publishing.'
if lesson not in lines:
    if len(lines)>=40: lines[-1]+=' '+lesson
    else: lines.append(lesson)
lessons.write_text('\n'.join(lines)+'\n')
checks=json.loads((e/'independent-results.json').read_text())['checks']
assert len(checks)==42 and sum(c['pass'] for c in checks)==40
assert (e/'suite.exit').read_text().strip()=='0'
assert (e/'independent.exit').read_text().strip()=='1'
assert (e/'scan-rotation.exit').read_text().strip()=='1'
print('Verified: 715 suite passes, 40/42 independent checks, two reproduced defects.')
