import json
import urllib.request
import urllib.error
from pathlib import Path
checks = []
for endpoint, expected in [('/', 404), ('/mcp', 401), ('/.well-known/oauth-protected-resource/mcp', 200)]:
    try:
        response = urllib.request.urlopen('http://127.0.0.1:19927' + endpoint, timeout=10)
    except urllib.error.HTTPError as response_error:
        response = response_error
    checks.append({'path': endpoint, 'status': response.status, 'expected': expected, 'pass': response.status == expected})
Path(__file__).resolve().parent.joinpath('12c554f/http-results.json').write_text(json.dumps(checks, indent=2))
print(json.dumps(checks))
assert all(check['pass'] for check in checks)
