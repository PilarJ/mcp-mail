from pathlib import Path
root = Path.cwd()
out = root / 'evidence/mcp-mail-14-4b4a04b-followup'
old = root / 'evidence/mcp-mail-14-acb569b-routine'
for filename in ['check-independent.py', 'check-scan-rotation.py']:
    source = (old / filename).read_text()
    source = source.replace('19914', '19924').replace('acb569bc9b182c3679f4deaf9300c507eb1e1dff', '4b4a04b825667bc65237267563f2b99fa494fbbb')
    source = source.replace('QA-acb569b', 'QA-followup-4b4a04b').replace('QA-rotation-acb569b', 'QA-rotation-followup')
    (out / filename).write_text(source)
