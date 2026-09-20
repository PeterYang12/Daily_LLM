"""Persist access to only this VM and expose authenticated API/data ports locally."""
import json,subprocess,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
s=json.loads((ROOT/'state.json').read_text())
def run(args,**kwargs):return subprocess.run(args,check=True,**kwargs)
xml=ET.fromstring(subprocess.check_output(['sudo','-n','virsh','net-dumpxml','default'],text=True))
matches=[x for x in xml.findall('.//dhcp/host') if x.get('mac')==s['vm_mac']]
if matches:
    assert matches[0].get('ip')==s['vm_ip']
else:
    assert not any(x.get('ip')==s['vm_ip'] for x in xml.findall('.//dhcp/host'))
    host=f"<host mac='{s['vm_mac']}' name='ua-e2b-node' ip='{s['vm_ip']}'/>"
    run(['sudo','-n','virsh','net-update','default','add-last','ip-dhcp-host',host,'--live','--config'])
run(['sudo','-n','virsh','autostart',s['vm_name']])
unit=f'''[Unit]
Description=Local E2B API and Kubernetes SSH tunnels
Wants=network-online.target
After=network-online.target libvirtd.service docker.service
[Service]
Type=simple
User=yuhanya
ExecStart=/usr/bin/ssh -NT -o ExitOnForwardFailure=yes -F {ROOT}/configs/ssh_config -L 127.0.0.1:18090:127.0.0.1:3000 -L 172.30.90.1:18090:127.0.0.1:3000 -L 127.0.0.1:18091:127.0.0.1:3002 -L 172.30.90.1:18091:127.0.0.1:3002 -L 127.0.0.1:18092:127.0.0.1:6443 ua-e2b
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
'''
path=ROOT/'configs/ua-e2b-local-tunnel.service';path.write_text(unit)
target=Path('/etc/systemd/system/ua-e2b-local-tunnel.service')
if target.exists():
    current=target.read_text()
    assert str(ROOT) in current,'Existing unit belongs to a different deployment'
run(['sudo','-n','install','-m','644',str(path),str(target)])
run(['sudo','-n','systemctl','daemon-reload'])
run(['sudo','-n','systemctl','enable','--now','ua-e2b-local-tunnel.service'])
print('Access tunnels configured: host 127.0.0.1:18090/18091; coordinator 172.30.90.1:18090/18091')
