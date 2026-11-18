import asyncio
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
import pytest
from fastmcp import Client, FastMCP
repo = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(repo))
from tests.test_mark_seen_on_get import SeenTrackingServer, message_with_attachment, make_settings, SEEN, MESSAGE_ID
from mail_mcp import imap as imap_module, tools
from mail_mcp.imap import ImapClient

# No socket is needed by the IMAP test double or the in-memory MCP transport.
def forbid_network(event, args):
    if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo', 'socket.bind', 'socket.sendto'}:
        raise AssertionError('QA test attempted network access: ' + event)
sys.addaudithook(forbid_network)

async def invoke(settings, tool, arguments):
    mcp = FastMCP('QA isolated test')
    tools.register(mcp, settings)
    async with Client(mcp) as client:
        listed = {t.name: t for t in await client.list_tools()}
        result = await client.call_tool(tool, arguments)
    assert not result.is_error, result
    return result.structured_content or result.data, listed['mail_get']

async def main():
    rows, untouched, descriptions = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        for setting in (False, True):
            for value in ('omitted', None, False, True):
                for initially_seen in (False, True):
                    wire = SeenTrackingServer({7: message_with_attachment(), 8: message_with_attachment()})
                    original = {r'\Flagged', r'\Answered', 'qa_keep'} | ({SEEN} if initially_seen else set())
                    wire.flags[7] = set(original)
                    sentinel = {r'\Flagged', 'qa_other'}
                    wire.flags[8] = set(sentinel)
                    with pytest.MonkeyPatch.context() as patch:
                        patch.setattr(imap_module, 'MailBox', wire.mailbox_class())
                        settings = make_settings(tmp, MARK_SEEN_ON_GET=str(setting).lower())
                        args = {'uid': '7'}
                        if value != 'omitted':
                            args['mark_seen'] = value
                        result, tool = await invoke(settings, 'mail_get', args)
                    effective = setting if value is None or value == 'omitted' else value
                    assert result['subject'] == 'Invoice'
                    expected = original | ({SEEN} if effective else set())
                    assert wire.flags[7] == expected, (setting, value, wire.flags)
                    assert wire.flags[8] == sentinel, wire.flags
                    assert not wire.mutations(), wire.mutations()
                    schema = tool.inputSchema['properties']['mark_seen']
                    assert schema['default'] is None and 'mark_seen' not in tool.inputSchema.get('required', [])
                    assert {v.get('type') for v in schema['anyOf']} == {'boolean', 'null'}
                    assert 'MARK_SEEN_ON_GET' in schema['description']
                    phrase = 'this mailbox marks it read' if setting else 'this mailbox does not mark it read'
                    assert phrase in tool.description
                    rows.append({'instance': setting, 'call': value, 'initially_seen': initially_seen,
                                 'effective': effective, 'flags_before': sorted(original), 'flags_after': sorted(wire.flags[7]),
                                 'other_uid_unchanged': True})
                    if value == 'omitted' and not initially_seen:
                        descriptions.append({'instance': setting, 'description': tool.description, 'schema': schema})
            for initially_seen in (False, True):
                scenarios = [('mail_list', {}), ('mail_list', {'unread_only': True}),
                             ('mail_search', {'criteria': 'TEXT "invoice"'}),
                             ('mail_get_thread', {'message_id': MESSAGE_ID}),
                             ('mail_get_attachment', {'uid': '7', 'filename': 'invoice.pdf'}),
                             ('approval_candidates', {})]
                for tool_name, args in scenarios:
                    wire = SeenTrackingServer({7: message_with_attachment()})
                    original = {r'\Flagged', 'qa_keep'} | ({SEEN} if initially_seen else set())
                    wire.flags[7] = set(original)
                    with pytest.MonkeyPatch.context() as patch:
                        patch.setattr(imap_module, 'MailBox', wire.mailbox_class())
                        settings = make_settings(tmp, MARK_SEEN_ON_GET=str(setting).lower())
                        if tool_name == 'approval_candidates':
                            found = await ImapClient(settings).approval_candidates('sender@example.test', date(2026, 10, 1), 10, 1000000)
                            assert [m.uid for m in found] == ['7']
                        else:
                            result, _ = await invoke(settings, tool_name, args)
                            if tool_name == 'mail_get_attachment':
                                assert result['filename'] == 'invoice.pdf'
                            else:
                                assert result['count'] >= 1
                    assert wire.flags[7] == original, (tool_name, wire.flags)
                    assert not wire.mutations(), wire.mutations()
                    fetches = [c.line.decode('ascii').strip() for c in wire.named('UID FETCH')]
                    assert fetches and all('BODY[' not in cmd.upper() for cmd in fetches), fetches
                    untouched.append({'tool': tool_name, 'instance': setting, 'initially_seen': initially_seen,
                                      'flags_before': sorted(original), 'flags_after': sorted(wire.flags[7]), 'fetches': fetches})
    return {'mail_get_matrix': rows, 'other_reads_and_approval': untouched, 'descriptions': descriptions}

output = asyncio.run(main())
# Start the real entry point with a completely synthetic environment. An audit hook in
# sitecustomize forbids socket use even if startup validation were to regress.
with tempfile.TemporaryDirectory() as tmp:
    temp = Path(tmp)
    (temp / 'sitecustomize.py').write_text("import sys\ndef guard(event,args):\n if event.startswith('socket.'):\n  raise RuntimeError('QA startup forbids network access')\nsys.addaudithook(guard)\n")
    env = {'PATH': os.environ.get('PATH', ''), 'HOME': tmp,
           'PYTHONPATH': str(temp) + os.pathsep + str(repo / 'src'),
           'MAILBOX_LABEL': 'box@example.test', 'BASE_URL': 'https://mcp.example.test',
           'GITHUB_CLIENT_ID': 'test', 'GITHUB_CLIENT_SECRET': 'test', 'ALLOWED_GITHUB_LOGINS': 'test',
           'IMAP_HOST': 'imap.example.test', 'IMAP_USER': 'box@example.test', 'IMAP_PASS': 'test',
           'SMTP_HOST': 'smtp.example.test', 'SMTP_USER': 'box@example.test', 'SMTP_PASS': 'test', 'DATA_DIR': tmp}
    startup = []
    for raw in ('maybe', '2', 'truee', 'none'):
        result = subprocess.run([sys.executable, '-m', 'mail_mcp.server'], cwd=tmp,
                                env={**env, 'MARK_SEEN_ON_GET': raw}, capture_output=True, text=True, timeout=15)
        (out / ('startup-' + raw + '.stderr')).write_text(result.stderr)
        assert result.returncode != 0 and 'MARK_SEEN_ON_GET' in result.stderr and 'valid boolean' in result.stderr, result.stderr
        assert 'QA startup forbids network access' not in result.stderr
        startup.append({'value': raw, 'exit_code': result.returncode, 'error_names_setting': True, 'error_explains_boolean': True,
                        'network_attempted': False})
    output['startup'] = startup
output['runtime'] = {'python': platform.python_version(), **{name: importlib.metadata.version(name) for name in
                                                        ('fastmcp', 'imap-tools', 'pydantic', 'pydantic-settings', 'pytest')}}
(out / 'independent-results.json').write_text(json.dumps(output, indent=2) + '\n')
print(json.dumps({'mail_get_cases': len(output['mail_get_matrix']), 'untouched_read_cases': len(output['other_reads_and_approval']),
                  'invalid_startups': len(output['startup']), 'runtime': output['runtime'], 'result': 'PASS'}, indent=2))
