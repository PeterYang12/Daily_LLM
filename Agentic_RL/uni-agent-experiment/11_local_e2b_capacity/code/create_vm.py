"""Create only the named local E2B VM; never alter existing domains."""
import io,json,subprocess
from pathlib import Path
import pycdlib

ROOT=Path(__file__).resolve().parents[1]
state=json.loads((ROOT/'state.json').read_text())
name=state['vm_name'];vmroot=Path('/var/lib/libvirt/images')/name
def run(args,**kwargs):return subprocess.run(args,check=True,**kwargs)
probe=subprocess.run(['sudo','-n','virsh','dominfo',name],capture_output=True)
if probe.returncode==0:raise RuntimeError('Domain already exists; inspect it before replaying this creator')
key=(Path('/home/yuhanya/uni-agent-lab/secrets/local-e2b-large/id_ed25519.pub')).read_text().strip()
user_data=f'''#cloud-config
hostname: ua-e2b-large
manage_etc_hosts: true
users:
  - name: e2b
    groups: [sudo]
    sudo: ['ALL=(ALL) NOPASSWD:ALL']
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - {key}
ssh_pwauth: false
disable_root: true
package_update: false
package_upgrade: false
'''
meta_data=f'instance-id: {name}\nlocal-hostname: ua-e2b-large\n'
for file,content in [('user-data',user_data),('meta-data',meta_data)]:
    (ROOT/'configs'/file).write_text(content)
iso=pycdlib.PyCdlib();iso.new(interchange_level=3,joliet=3,vol_ident='cidata',rock_ridge='1.09')
for filename,content in [('user-data',user_data),('meta-data',meta_data)]:
    data=content.encode();iso.add_fp(io.BytesIO(data),len(data),iso_path='/'+filename.upper().replace('-','_')+';1',rr_name=filename,joliet_path='/'+filename)
iso.write(str(ROOT/'configs/seed.iso'));iso.close()
run(['sudo','-n','mkdir','-p',str(vmroot)])
image=ROOT/'downloads/ubuntu-24.04-server-cloudimg-amd64.img'
run(['sudo','-n','qemu-img','convert','-p','-O','qcow2',str(image),str(vmroot/'root.qcow2')])
run(['sudo','-n','qemu-img','resize',str(vmroot/'root.qcow2'),str(state['vm_disk_gib'])+'G'])
run(['sudo','-n','cp',str(ROOT/'configs/seed.iso'),str(vmroot/'seed.iso')])
xml=f'''<domain type='kvm'>
  <name>{name}</name>
  <description>Owned local E2B sandbox experiment; {ROOT}</description>
  <memory unit='GiB'>{state['vm_memory_gib']}</memory>
  <currentMemory unit='GiB'>{state['vm_memory_gib']}</currentMemory>
  <vcpu placement='static'>{state['vm_vcpus']}</vcpu>
  <os><type arch='x86_64' machine='pc-q35-6.2'>hvm</type><boot dev='hd'/></os>
  <features><acpi/><apic/></features>
  <cpu mode='host-passthrough' check='none' migratable='off'><topology sockets='1' cores='96' threads='1'/><feature policy='require' name='svm'/></cpu>
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff><on_reboot>restart</on_reboot><on_crash>destroy</on_crash>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'><driver name='qemu' type='qcow2' cache='none' discard='unmap'/><source file='{vmroot}/root.qcow2'/><target dev='vda' bus='virtio'/></disk>
    <disk type='file' device='cdrom'><driver name='qemu' type='raw'/><source file='{vmroot}/seed.iso'/><target dev='sda' bus='sata'/><readonly/></disk>
    <interface type='network'><mac address='{state['vm_mac']}'/><source network='default'/><model type='virtio'/></interface>
    <serial type='pty'><log file='{vmroot}/console.log' append='on'/><target port='0'/></serial>
    <console type='pty'><target type='serial' port='0'/></console>
    <channel type='unix'><target type='virtio' name='org.qemu.guest_agent.0'/></channel>
    <memballoon model='virtio'/>
    <rng model='virtio'><backend model='random'>/dev/urandom</backend></rng>
  </devices>
</domain>'''
(ROOT/'configs/domain.xml').write_text(xml)
run(['sudo','-n','virsh','define',str(ROOT/'configs/domain.xml')])
run(['sudo','-n','virsh','start',name])
state.update(status='vm_booting',vm_disk_directory=str(vmroot))
(ROOT/'state.json').write_text(json.dumps(state,indent=2)+'\n')
