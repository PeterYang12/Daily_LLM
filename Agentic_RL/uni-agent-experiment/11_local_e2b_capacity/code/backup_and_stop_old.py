"""Cold-export E2B application state, then stop only the retired small VM."""
import hashlib,json,os,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OLD=Path('/home/yuhanya/uni-agent-lab/local-e2b-20260916')
PRIVATE=Path('/home/yuhanya/uni-agent-lab/secrets/local-e2b-large')
SSH=['ssh','-F',str(OLD/'configs/ssh_config'),'ua-e2b']
pods=json.loads(subprocess.check_output(SSH+['sudo k3s kubectl -n e2b get pods -o json'],text=True))
assert not pods['items'],'Wait for the old E2B Pod to stop before exporting databases'
check=subprocess.run(SSH+["sudo pgrep -x 'postgres|clickhouse|orchestrator|firecracker'"],capture_output=True,text=True)
assert check.returncode==1,check.stdout
subprocess.run(SSH+['sudo sync -f /var/lib/e2b'],check=True)
archive=PRIVATE/'e2b-state.tar';partial=PRIVATE/'e2b-state.tar.partial'
fd=os.open(partial,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'wb') as stream:
    subprocess.run(SSH+['sudo tar --sparse --numeric-owner -C / -cpf - var/lib/e2b'],stdout=stream,check=True)
partial.rename(archive)
sha=hashlib.sha256()
with archive.open('rb') as stream:
    while chunk:=stream.read(8*1024*1024):sha.update(chunk)
record={'time_unix':time.time(),'old_vm':'ua-e2b-local-20260916','old_application_stopped_before_backup':True,
        'application_archive_bytes':archive.stat().st_size,'application_archive_sha256':sha.hexdigest(),'archive_location':'protected secrets/local-e2b-large/e2b-state.tar',
        'templates_and_databases_preserved':True}
(ROOT/'results/migration-backup.json').write_text(json.dumps(record,indent=2)+'\n')
subprocess.run(['sudo','-n','systemctl','disable','--now','ua-e2b-local-tunnel.service'],check=True)
subprocess.run(['sudo','-n','virsh','autostart','--disable','ua-e2b-local-20260916'],check=True)
subprocess.run(['sudo','-n','virsh','shutdown','ua-e2b-local-20260916'],check=True)
deadline=time.monotonic()+120
while time.monotonic()<deadline:
    state=subprocess.check_output(['sudo','-n','virsh','domstate','ua-e2b-local-20260916'],text=True).strip()
    if state=='shut off':break
    time.sleep(2)
assert state=='shut off',state
record['old_vm_stopped']=True;record['old_disk_retained']=True
(ROOT/'results/migration-backup.json').write_text(json.dumps(record,indent=2)+'\n')
data=json.loads((OLD/'state.json').read_text());data.update(status='retired',replacement_vm='ua-e2b-96c512g-20260916',retired_unix=time.time(),old_vm_autostart=False)
(OLD/'state.json').write_text(json.dumps(data,indent=2)+'\n')
print(json.dumps(record))
