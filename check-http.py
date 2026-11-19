import json
import urllib.request
import urllib.error
from pathlib import Path
checks = []
for revision, port in [('acb569b', 19925), ('4b4a04b', 19926)]:
    for endpoint, expected in [('/', 404), ('/mcp', 401), ('/.well-known/oauth-protected-resource/mcp', 200)]:
        try:
            response = urllib.request.urlopen(f'http://127.0.0.1:{port}{endpoint}', timeout=10)
        except urllib.error.HTTPError as response_error:
            response = response_error
        payload = response.read().decode()
        actual = response.status
        checks.append({'revision': revision, 'path': endpoint, 'status': actual, 'expected': expected, 'pass': actual == expected, 'body': payload})
        print(f'{revision} {endpoint}: {actual} expected {expected}')
Path(__file__).with_name('http-results.json').write_text(json.dumps(checks, indent=2))
assert all(check['pass'] for check in checks)
