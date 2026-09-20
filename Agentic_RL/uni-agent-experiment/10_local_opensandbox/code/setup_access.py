"""Expose only the authenticated OpenSandbox API through managed local tunnels."""
import json,os,subprocess,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=json.loads((ROOT/'state.json').read_text())
def run(args):return subprocess.run(args,check=True)
xml=ET.fromstring(subprocess.check_output(['sudo','-n','virsh','net-dumpxml','default'],text=True))
entries=xml.findall('.//dhcp/host')
if not any(x.get('mac')==s['vm_mac'] for x in entries):
    assert not any(x.get('ip')==s['vm_ip'] for x in entries)
    host=f"<host mac='{s['vm_mac']}' name='ua-osb-node' ip='{s['vm_ip']}'/>"
    run(['sudo','-n','virsh','net-update','default','add-last','ip-dhcp-host',host,'--live','--config'])
run(['sudo','-n','virsh','autostart',s['vm_name']])
unit=f'''[Unit]
Description=Local OpenSandbox API and Kubernetes SSH tunnels
Wants=network-online.target
After=network-online.target libvirtd.service docker.service
[Service]
Type=simple
User=yuhanya
ExecStart=/usr/bin/ssh -NT -o ExitOnForwardFailure=yes -F {ROOT}/configs/ssh_config -L 127.0.0.1:18094:{s['vm_ip']}:30808 -L 172.30.90.1:18094:{s['vm_ip']}:30808 -L 127.0.0.1:18095:127.0.0.1:6443 ua-osb
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
'''
path=ROOT/'configs/ua-opensandbox-local-tunnel.service';path.write_text(unit)
dest=Path('/etc/systemd/system/ua-opensandbox-local-tunnel.service')
if dest.exists():assert str(ROOT) in dest.read_text()
run(['sudo','-n','install','-m','644',str(path),str(dest)])
run(['sudo','-n','systemctl','daemon-reload']);run(['sudo','-n','systemctl','enable','--now','ua-opensandbox-local-tunnel.service'])
secret=Path('/home/yuhanya/uni-agent-lab/secrets/local-opensandbox');key=(secret/'api-key').read_text().strip()
for name,domain in [('host.env','127.0.0.1:18094'),('container.env','172.30.90.1:18094')]:
    content=f'OPENSANDBOX_API_KEY={key}\nOPENSANDBOX_DOMAIN={domain}\nOPENSANDBOX_PROTOCOL=http\n'
    path=secret/name;fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:f.write(content)
    path.chmod(0o600)
print('OpenSandbox API tunnel and protected client settings configured')
