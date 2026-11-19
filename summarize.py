import json
from pathlib import Path
out = Path(__file__).resolve().parent
for name in ['formats-acb569b.json', 'formats-4b4a04b.json', 'independent-results.json', 'scan-rotation-results.json']:
    path = out / name
    if not path.exists():
        print(name, 'still running')
        continue
    data = json.loads(path.read_text())
    print(name)
    if 'checks' in data:
        checks = data['checks']
        print(json.dumps({'passed': sum(c['pass'] for c in checks), 'total': len(checks), 'failures': [c for c in checks if not c['pass']]}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False)[:1000])
    if name.startswith('formats'):
        record = data['responses']['dlouhý-text.txt']
        print(json.dumps({key: value for key, value in record.items() if key not in ['text', 'UNTRUSTED_CONTENT_NOTE']}, ensure_ascii=False))
