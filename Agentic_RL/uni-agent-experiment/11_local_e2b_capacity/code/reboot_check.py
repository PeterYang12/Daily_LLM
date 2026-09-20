"""Check full-VM restart recovery with zero live sandboxes before restarting."""
import json,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SSH=['ssh','-F',str(ROOT/'configs/ssh_config'),'ua-e2b-large']
env=dict(x.split('=',1) for x in (ROOT.parent/'secrets/local-e2b/host.env').read_text().splitlines())
def get(path):
    req=urllib.request.Request(env['E2B_API_URL']+path,headers={'X-API-Key':env['E2B_API_KEY']})
    with urllib.request.urlopen(req,timeout=5) as response:return response.read()
assert json.loads(get('/sandboxes'))==[], 'Only restart an idle platform'
before=subprocess.check_output(SSH+['cat /proc/sys/kernel/random/boot_id'],text=True).strip()
t=time.monotonic()
subprocess.run(SSH+['sudo reboot'],capture_output=True)
deadline=t+300
while time.monotonic()<deadline:
    time.sleep(5)
    r=subprocess.run(SSH+['cat /proc/sys/kernel/random/boot_id'],text=True,capture_output=True)
    if r.returncode or r.stdout.strip()==before:continue
    after=r.stdout.strip()
    try:
        assert get('/health')
        pod=json.loads(subprocess.check_output(SSH+['sudo k3s kubectl -n e2b get pod e2b-0 -o json'],text=True,stderr=subprocess.DEVNULL))
        if not any(c['type']=='Ready' and c['status']=='True' for c in pod['status']['conditions']):continue
        break
    except Exception:continue
else:raise TimeoutError('New E2B VM did not recover within 300 seconds')
report={'before_boot_id':before,'after_boot_id':after,'recovered_s':time.monotonic()-t,'pod_ready':True,'api_healthy':True,'original_api_key_works':True,'success':before!=after}
(ROOT/'results/reboot-check.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
