"""Cold-restore the retired E2B application into the new VM, preserving credentials."""
import hashlib,json,subprocess,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT.parent/'secrets/local-e2b-large'
SSH=['ssh','-F',str(ROOT/'configs/ssh_config'),'ua-e2b-large']
def run(command,**kwargs):
    return subprocess.run(SSH+[command],check=True,**kwargs)

backup=json.loads((ROOT/'results/migration-backup.json').read_text())
assert backup['old_vm_stopped'] and backup['old_application_stopped_before_backup']
archive=PRIVATE/'e2b-state.tar'
assert archive.stat().st_size==backup['application_archive_bytes']
with archive.open('rb') as f:
    digest=hashlib.sha256()
    for chunk in iter(lambda:f.read(8*1024*1024),b''):
        digest.update(chunk)
    assert digest.hexdigest()==backup['application_archive_sha256']
run('test ! -e /var/lib/e2b && sudo k3s kubectl create namespace e2b --dry-run=client -o yaml | sudo k3s kubectl apply -f -')
# Explicitly reject overwriting an already restored or running instance.
run('test ! -e /var/lib/e2b')
with archive.open('rb') as f:
    run('sudo tar --sparse --numeric-owner -C / -xpf - && sudo sync -f /var/lib/e2b',stdin=f)
secret=json.loads((PRIVATE/'migrated-api-secret.json').read_text())
secret['metadata']={'name':'e2b-api','namespace':'e2b'}
secret.pop('status',None)
run('sudo k3s kubectl apply -f -',input=json.dumps(secret),text=True)
subprocess.run(['rsync','-a','-e','ssh -F '+str(ROOT/'configs/ssh_config'),str(ROOT/'configs/deploy')+'/', 'ua-e2b-large:/home/e2b/deploy/'],check=True)
subprocess.run(['scp','-F',str(ROOT/'configs/ssh_config'),str(ROOT/'code/deploy_e2b.sh'),'ua-e2b-large:/home/e2b/deploy_e2b.sh'],check=True)
(ROOT/'results/migration-restore.json').write_text(json.dumps({'restored_unix':time.time(),'cold_archive_verified':True,'application_data_restored':True,'api_secret_preserved':True,'source_archive_sha256':backup['application_archive_sha256']},indent=2)+'\n')
run('bash /home/e2b/deploy_e2b.sh')
