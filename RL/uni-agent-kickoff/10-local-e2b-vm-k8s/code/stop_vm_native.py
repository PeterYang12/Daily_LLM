"""Ask the guest to shut down through ACPI before systemd's stop deadline."""
import json
from pathlib import Path
import socket
import time

path = Path('/data2/yuhanya/e2b-local-20260916/vm/qmp.sock')
if path.exists():
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(5)
            conn.connect(str(path))
            stream = conn.makefile('rwb', buffering=0)
            stream.readline()
            for command in ['qmp_capabilities', 'system_powerdown']:
                stream.write(json.dumps({'execute': command}).encode() + b'\n')
                while True:
                    reply = json.loads(stream.readline())
                    if 'return' in reply or 'error' in reply:
                        break
        for _ in range(110):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(1)
                    probe.connect(str(path))
            except OSError:
                break
            time.sleep(1)
    except OSError:
        pass
