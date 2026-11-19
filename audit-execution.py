import json
import re
from pathlib import Path
out = Path(__file__).resolve().parent
latest = out / '12c554f'
head = '12c554f5abe89e30980d5399e1e28329ee3942de'
def read(path):
    return json.loads(path.read_text())
def successful(path):
    assert path.read_text().strip() == '0', str(path)
for filename in ['suite.exit', 'build-complete.exit', 'container-acb569b.exit', 'container-4b4a04b.exit', 'independent.exit', 'scan-rotation.exit', 'czech-pdf-acb569b.exit', 'czech-pdf-4b4a04b.exit']:
    successful(out / filename)
for filename in ['suite.exit', 'build.exit', 'container.exit', 'tests-complete.exit', 'formats-12c554f.exit', 'czech-pdf-12c554f.exit', 'independent.exit', 'scan-rotation.exit']:
    successful(latest / filename)
assert '729 passed' in (out / 'suite.log').read_text()
assert '756 passed' in (latest / 'suite.log').read_text()
original = out.parent / 'mcp-mail-14-acb569b-routine'
assert '715 passed' in (original / 'suite.log').read_text()
successful(original / 'suite.exit')
settlements = []
for revision in ['acb569b', '4b4a04b']:
    data = read(out / f'formats-{revision}.json')
    failures = [check for check in data['checks'] if not check['pass']]
    assert len(data['checks']) == 48 and len(failures) == 1
    assert failures[0]['name'] == 'české-stránky.pdf Czech text readable'
    corrected = read(out / f'czech-pdf-{revision}.json')
    assert len(corrected['checks']) == 4 and all(check['pass'] for check in corrected['checks'])
    settlements.append({'revision': revision, 'initial': '47/48; one invalid PDF-font fixture', 'correction': '4/4 with complete Czech glyph coverage and independent PDF reading', 'validChecks': '51/51', 'excludedFailureIsProductDefect': False})
    container = read(out / f'container-{revision}.json')
    assert len(container['checks']) == 19 and all(check['pass'] for check in container['checks'])
formats = read(latest / 'formats-12c554f.json')
assert len(formats['checks']) == 48 and all(check['pass'] for check in formats['checks'])
assert 'repos/mcp-mail-qa14-12c554f-followup/src' in formats['source']
assert all(check['pass'] for check in read(latest / 'czech-pdf-12c554f.json')['checks'])
independent = read(latest / 'independent-results.json')
assert len(independent['checks']) == 42 and all(check['pass'] for check in independent['checks'])
assert independent['head'] == head
assert read(latest / 'scan-rotation-results.json')['correct_orientation']
container = read(latest / 'container-results.json')
assert len(container['checks']) == 19 and all(check['pass'] for check in container['checks'])
assert all(check['pass'] for check in read(latest / 'http-results.json'))
assert all(check['pass'] for check in read(latest / 'html-delta-results.json'))
assert all(check['pass'] for check in read(out / 'http-results.json'))
report = {
    'requestedHead': 'acb569bc9b182c3679f4deaf9300c507eb1e1dff',
    'latestTestedHead': head,
    'requestedHeadVerdict': 'NOT-OK; original two P2 findings remain valid at acb569b',
    'latestFunctionalVerdict': 'PASS; both previous P2 findings reproduced as fixed',
    'suites': {'acb569b': 715, '4b4a04b': 729, '12c554f': 756},
    'latestChecks': {'allFormats': '48/48', 'independentRealImapMcp': '42/42', 'correctedCzechPdf': '4/4', 'rotatedScan': 'PASS', 'installedDockerFormats': '19/19', 'httpStartup': '3/3', 'htmlDelta': '4/4'},
    'helperFailureSettlements': settlements,
    'runtimeWarnings': ['One upstream Authlib deprecation in each full suite', 'Intentional corrupt PDF emits EOF marker not found; safe readable:false follows and the next call passes'],
    'unexecuted': ['Real customer mailboxes (explicitly excluded)', 'External OAuth login and SMTP sending', 'Physical devices'],
    'unfinishedAcceptanceCriteria': []
}
(out / 'execution-audit.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
