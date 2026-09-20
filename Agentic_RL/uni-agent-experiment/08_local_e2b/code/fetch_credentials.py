"""Store local E2B SDK configuration without printing credentials."""
import json,os,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
secret=Path('/home/yuhanya/uni-agent-lab/secrets/local-e2b')
key=subprocess.check_output(['ssh','-F',str(ROOT/'configs/ssh_config'),'ua-e2b',
    'sudo cat /var/lib/e2b/data/seed-state/team-api-key'],text=True).strip()
assert key.startswith('e2b_') and '\n' not in key
for name,host in [('host.env','127.0.0.1'),('container.env','172.30.90.1')]:
    data=f'E2B_API_KEY={key}\nE2B_API_URL=http://{host}:18090\nE2B_SANDBOX_URL=http://{host}:18091\n'
    path=secret/name
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:f.write(data)
    path.chmod(0o600)
print('Local SDK settings saved in secrets/local-e2b/{host,container}.env (mode 0600).')
