import json
import subprocess
from pathlib import Path
out = Path(__file__).resolve().parent
owned = [
    (out / 'dovecot-container.id', 'tester-mail14-dovecot-followup'),
    (out / 'app-acb569b-container.id', 'tester-mail14-app-acb569b-followup'),
    (out / 'app-4b4a04b-container.id', 'tester-mail14-app-4b4a04b-followup'),
    (out / '12c554f/container.id', 'tester-mail14-app-12c554f-followup'),
]
records = []
volumes = []
for file, name in owned:
    identifier = file.read_text().strip()
    info = json.loads(subprocess.check_output(['docker', 'inspect', identifier], text=True))[0]
    assert info['Id'] == identifier and info['Name'] == '/' + name
    mounts = [mount['Name'] for mount in info['Mounts'] if mount['Type'] == 'volume']
    volumes.extend(mounts)
    records.append({'name': name, 'id': identifier, 'image': info['Image'], 'volumes': mounts})
    logs = subprocess.run(['docker', 'logs', identifier], capture_output=True, text=True, check=True)
    (out / (name + '-final.log')).write_text(logs.stdout + logs.stderr)
for record in records:
    subprocess.run(['docker', 'stop', record['id']], check=True, capture_output=True)
    subprocess.run(['docker', 'rm', '-v', record['id']], check=True, capture_output=True)
remaining = subprocess.check_output(['docker', 'ps', '-a', '--format', '{{.Names}}'], text=True).splitlines()
assert not any(record['name'] in remaining for record in records)
remaining_volumes = subprocess.check_output(['docker', 'volume', 'ls', '--format', '{{.Name}}'], text=True).splitlines()
assert not set(volumes).intersection(remaining_volumes)
root = out.parent.parent
checkouts = {}
for folder in ['mcp-mail-qa14-acb569b-routine', 'mcp-mail-qa14-4b4a04b-followup', 'mcp-mail-qa14-12c554f-followup']:
    location = root / 'repos' / folder
    status = subprocess.check_output(['git', '-C', str(location), 'status', '--porcelain'], text=True)
    assert not status.strip(), (folder, status)
    checkouts[folder] = subprocess.check_output(['git', '-C', str(location), 'rev-parse', 'HEAD'], text=True).strip()
result = {'ownedContainersRemoved': records, 'anonymousVolumesRemoved': volumes, 'ownedResourcesAbsent': True, 'sourceCheckoutsUnchanged': checkouts}
(out / 'cleanup-verification.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'containersRemoved': len(records), 'volumesRemoved': len(volumes), 'allSourceCheckoutsClean': True}))
