import hashlib
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import sys
import mail_mcp.guard

root = Path(__file__).resolve().parent
workspace = root.parent.parent
head = workspace / 'repos/mcp-mail-qa7-1f486ba'
original = head / 'tests/test_send_limits.py'
copied = root / 'current-tests/tests/test_send_limits.py'
assert original.read_bytes() == copied.read_bytes()
assert Path(mail_mcp.guard.__file__).resolve() == head / 'src/mail_mcp/guard.py'
assert (root / 'full-suite.exit').read_text().strip() == '0'
assert (root / 'baseline-send-limits.exit').read_text().strip() == '1'
assert '660 passed, 1 warning' in (root / 'full-suite.log').read_text()
assert '33 failed, 42 passed' in (root / 'baseline-send-limits.log').read_text()
assert 'test_an_approval_request_does_not_replace_an_unreadable_log PASSED' in (root / 'full-suite.log').read_text()
result = {
    'head': '1f486ba78e592aec7a280d80f2264e2c89a334e3',
    'defect_baseline': '2492ac190214bcf1b12a0180a1afd852229f6161',
    'ci_checkout': '71ccefb0469a21a325fb891e93e5183bc102ba18',
    'ci_parents': ['657bbcbe55280a17b78c91d27f73b17395788327', '1f486ba78e592aec7a280d80f2264e2c89a334e3'],
    'head_and_ci_tree': '3d52e39551b00b8647e8c1fbfebc1a14629bee13',
    'python': sys.version,
    'platform': platform.platform(),
    'effective_uid': os.geteuid(),
    'implementation': str(Path(mail_mcp.guard.__file__).resolve()),
    'test_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
    'versions': {name: importlib.metadata.version(name) for name in ['pytest', 'fastmcp', 'anyio', 'pydantic', 'cryptography', 'dkimpy']},
    'full_suite': {'passed': 660, 'failed': 0, 'skipped': 0, 'warnings': 1},
    'baseline_current_limit_tests': {'passed': 42, 'failed': 33},
    'cleanup': 'No server, container or background process was started. Temporary fixture directories are owned and cleaned by pytest. Both product checkouts have clean tracked status.',
}
(root / 'provenance.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
