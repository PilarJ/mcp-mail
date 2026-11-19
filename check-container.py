import json
import mimetypes
from pathlib import Path
from mail_mcp import attachments
import mail_mcp
from importlib.metadata import version

text = 'Žluťoučký kůň, příliš žluťoučký příběh. Číslo 14, částka 300 Kč.'
checks = []
for file in sorted(Path('/tmp/qa-fixtures').iterdir()):
    if file.name == 'české-stránky.pdf':
        continue  # Invalid original fixture lacks five required Czech glyphs.
    mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
    expected_error = file.name in ['poškozené.pdf', 'nepodporovaný.zip']
    try:
        result = attachments.read(file.name, mime, file.read_bytes())
        good = not expected_error and (bool(result.images) if file.suffix == '.png' or file.name == 'sken.pdf' else text in result.text)
        checks.append({'name': file.name, 'pass': good, 'kind': result.kind})
    except attachments.Unreadable as error:
        checks.append({'name': file.name, 'pass': expected_error, 'detail': str(error)})
result = {'source': mail_mcp.__file__, 'dependencies': {name: version(name) for name in ['pypdf', 'pillow', 'defusedxml', 'fastmcp']}, 'checks': checks}
print(json.dumps(result, ensure_ascii=False, indent=2))
assert len(checks) == 19
assert all(item['pass'] for item in checks)
