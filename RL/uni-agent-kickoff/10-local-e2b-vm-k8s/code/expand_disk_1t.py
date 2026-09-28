"""Online expansion of this VM's active root disk to 1 TiB, with service checks."""
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import time
import uuid

import httpx
from e2b import Sandbox

ROOT = Path('/data2/yuhanya/e2b-local-20260916')
TARGET = 1024 * 2**30
IMAGE = ROOT / 'vm/root.qcow2'
OUT = Path((ROOT / 'run/latest-disk-resize.txt').read_text().strip())
SETTINGS = Path('/home/yuhanya/.config/e2b-local-20260916/client.json')


def guest(command, timeout=30):
    result = subprocess.run([str(ROOT / 'code/vm-ssh'), command], capture_output=True,
                            text=True, timeout=timeout, check=True)
    return result.stdout


def qmp(command, arguments=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(30)
        connection.connect(str(ROOT / 'vm/qmp.sock'))
        stream = connection.makefile('rwb', buffering=0)
        json.loads(stream.readline())
        def call(name, args=None):
            request = {'execute': name}
            if args is not None:
                request['arguments'] = args
            stream.write(json.dumps(request).encode() + b'\n')
            while True:
                response = json.loads(stream.readline())
                if 'error' in response:
                    raise RuntimeError(response['error'])
                if 'return' in response:
                    return response['return']
        call('qmp_capabilities')
        return call(command, arguments)


def main():
    config = json.loads(SETTINGS.read_text())
    report = {'started_utc': datetime.now(timezone.utc).isoformat(), 'target_bytes': TARGET,
              'image_path': str(IMAGE), 'status': 'running'}
    sandbox = None
    try:
        blocks = qmp('query-block')
        matching = [b for b in blocks if b.get('inserted', {}).get('image', {}).get('filename') == str(IMAGE)]
        assert len(matching) == 1
        block = matching[0]
        old_size = block['inserted']['image']['virtual-size']
        assert old_size <= TARGET, 'This operation never shrinks a disk'
        assert qmp('query-status')['running']
        before = json.loads(guest('sudo sfdisk --json /dev/vda'))
        (OUT / 'partition-before.json').write_text(json.dumps(before, indent=2))
        assert guest('findmnt -no SOURCE,FSTYPE /').strip() == '/dev/vda1 ext4'
        boot = guest('cat /proc/sys/kernel/random/boot_id').strip()
        marker = uuid.uuid4().hex
        sandbox = Sandbox.create('base', timeout=600, request_timeout=30,
                                 metadata={'purpose': 'online-disk-expansion-continuity'}, **config)
        sandbox.files.write('/tmp/resize-marker', marker)
        report.update(old_bytes=old_size, sandbox_id=sandbox.sandbox_id, guest_boot_id_before=boot)
        print('Preflight passed; live sandbox continuity marker created', flush=True)
        if old_size < TARGET:
            qmp('block_resize', {'node-name': block['inserted']['node-name'], 'size': TARGET})
        after_blocks = qmp('query-block')
        (OUT / 'qmp-after.json').write_text(json.dumps(after_blocks, indent=2))
        for _ in range(15):
            if int(guest('sudo blockdev --getsize64 /dev/vda').strip()) == TARGET:
                break
            time.sleep(1)
        assert int(guest('sudo blockdev --getsize64 /dev/vda').strip()) == TARGET
        report['growpart'] = guest('sudo growpart /dev/vda 1', timeout=60)
        print(report['growpart'].strip(), flush=True)
        report['resize2fs'] = guest('sudo resize2fs /dev/vda1', timeout=180)
        print(report['resize2fs'].strip(), flush=True)
        after = json.loads(guest('sudo sfdisk --json /dev/vda'))
        (OUT / 'partition-after.json').write_text(json.dumps(after, indent=2))
        old_parts = {p['node']: p for p in before['partitiontable']['partitions']}
        new_parts = {p['node']: p for p in after['partitiontable']['partitions']}
        assert old_parts.keys() == new_parts.keys()
        for name, part in old_parts.items():
            assert all(part[k] == new_parts[name][k] for k in ['start', 'type', 'uuid'])
            assert new_parts[name]['size'] >= part['size']
            if name != '/dev/vda1':
                assert new_parts[name]['size'] == part['size']
        report['filesystem'] = guest('df -B1 --output=source,size,used,avail,target /')
        report['guest_boot_id_after'] = guest('cat /proc/sys/kernel/random/boot_id').strip()
        assert report['guest_boot_id_after'] == boot, 'Unexpected VM reboot during online resize'
        result = sandbox.commands.run('cat /tmp/resize-marker', timeout=15)
        assert result.stdout == marker
        report['existing_sandbox_preserved'] = True
        sandbox.kill()
        sandbox = None
        with httpx.Client(timeout=15, trust_env=False) as client:
            report['health_http'] = client.get(config['api_url'] + '/health').status_code
            report['authenticated_api_http'] = client.get(config['api_url'] + '/sandboxes',
                headers={'X-API-KEY': config['api_key']}).status_code
        assert report['health_http'] == report['authenticated_api_http'] == 200
        current = json.loads(SETTINGS.read_text())
        report['api_url'] = current['api_url']
        report['api_key_unchanged'] = current['api_key'] == config['api_key']
        assert report['api_key_unchanged'] and current['api_url'] == config['api_url']
        report.update(status='passed', completed_utc=datetime.now(timezone.utc).isoformat())
    except BaseException as error:
        report.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        if sandbox is not None:
            sandbox.kill()
        (OUT / 'result.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
