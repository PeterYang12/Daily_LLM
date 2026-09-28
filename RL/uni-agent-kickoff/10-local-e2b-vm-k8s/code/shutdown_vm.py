#!/usr/bin/env python3
"""Ask a QEMU guest to power off through its own QMP socket."""
import argparse
import json
from pathlib import Path
import socket
import time


def poweroff(path: Path, wait_seconds: float = 110) -> None:
    if not path.exists():
        return
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(str(path))
            stream = connection.makefile('rwb', buffering=0)
            json.loads(stream.readline())
            for command in ['qmp_capabilities', 'system_powerdown']:
                stream.write((json.dumps({'execute': command, 'id': command}) + '\n').encode())
                while True:
                    line = stream.readline()
                    if not line:
                        return
                    reply = json.loads(line)
                    if reply.get('id') == command:
                        if 'error' in reply:
                            raise RuntimeError('QMP rejected ' + command)
                        break
            stream.close()
    except (FileNotFoundError, ConnectionRefusedError):
        return
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                probe.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError):
            return
        time.sleep(1)
    raise TimeoutError('Guest did not power off before the shutdown deadline')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('qmp_socket', type=Path)
    args = parser.parse_args()
    poweroff(args.qmp_socket)
