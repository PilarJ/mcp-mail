import json
import time
from pathlib import Path
from mail_mcp.render import html_to_text
samples = {'unclosed_script': '<script>' * 200000, 'unclosed_style': '<style>' * 200000, 'unclosed_tags': '<' * 1500000}
checks = []
for name, content in samples.items():
    started = time.perf_counter()
    result = html_to_text(content)
    elapsed = time.perf_counter() - started
    checks.append({'name': name, 'bytes': len(content), 'seconds': elapsed, 'output_chars': len(result), 'pass': elapsed < 5})
expected = 'Český text\nDruhá část'
actual = html_to_text('<p>Český text</p><script>skip()</script><style>.skip{}</style><p>Druhá část</p>')
checks.append({'name': 'Czech HTML content survives script and style stripping', 'pass': actual == expected, 'actual': actual})
out = Path(__file__).resolve().parent / '12c554f/html-delta-results.json'
out.write_text(json.dumps(checks, ensure_ascii=False, indent=2))
print(json.dumps(checks, ensure_ascii=False))
assert all(check['pass'] for check in checks)
