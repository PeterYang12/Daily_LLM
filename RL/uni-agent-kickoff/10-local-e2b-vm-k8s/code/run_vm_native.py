"""Run the dedicated VM as an unprivileged systemd service."""
import json
import os
from pathlib import Path
import socket

root = Path('/data2/yuhanya/e2b-local-20260916')
cfg = json.loads((root / 'configs/deployment.json').read_text())
disk = Path(cfg.get('root_image', str(root / 'vm/root.qcow2')))
assert disk.is_file(), f'Missing active VM disk: {disk}'
for host, port in [('127.0.0.1', cfg['vm_ssh_port']), (cfg['host_ip'], 8088)]:
    with socket.socket() as check:
        check.bind((host, port))
qmp = root / 'vm/qmp.sock'
if qmp.exists():
    # A live VM holds the SSH/API ports checked above and the disk lock below.
    qmp.unlink()
cmd = ['/usr/bin/qemu-system-x86_64', '-name', 'e2b-sandbox-vm',
       '-machine', 'q35,accel=kvm,mem-merge=off', '-cpu', 'host,migratable=off,svm=on',
       '-smp', '192,sockets=1,cores=192,threads=1', '-m', str(cfg['vm_memory_gib']) + 'G',
       '-drive', f'file={disk},format=qcow2,if=virtio,cache=none,aio=threads,discard=unmap',
       '-drive', f'file={root}/vm/seed.iso,format=raw,if=virtio,readonly=on',
       '-nic', f"user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:{cfg['vm_ssh_port']}-:22,hostfwd=tcp:{cfg['host_ip']}:8088-:8088",
       '-display', 'none', '-serial', f'file:{root}/logs/vm-console.log',
       '-qmp', f'unix:{qmp},server=on,wait=off']
(root / 'configs/vm-native-command.json').write_text(json.dumps(cmd, indent=2))
os.execv(cmd[0], cmd)
