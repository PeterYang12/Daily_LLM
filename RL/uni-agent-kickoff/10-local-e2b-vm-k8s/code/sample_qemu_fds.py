"""Bounded, read-only descriptor sampling during a single capacity check."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

parser = argparse.ArgumentParser()
parser.add_argument('--pid', type=int, required=True)
parser.add_argument('--until-report', type=Path, required=True)
parser.add_argument('--seconds', type=int, default=600)
args = parser.parse_args()
proc = Path('/proc') / str(args.pid)
identity = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
deadline = time.monotonic() + args.seconds
while time.monotonic() < deadline:
    if (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19] != identity:
        raise SystemExit('QEMU process identity changed')
    limits = next(line.split() for line in (proc / 'limits').read_text().splitlines()
                  if line.startswith('Max open files'))
    print(json.dumps({'utc': datetime.now(timezone.utc).isoformat(),
                      'pid': args.pid, 'fd_count': len(os.listdir(proc / 'fd')),
                      'nofile_soft': int(limits[3]), 'nofile_hard': int(limits[4])}), flush=True)
    try:
        report = json.loads(args.until_report.read_text())
        if report.get('finished_utc') and report.get('status') != 'running':
            break
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    time.sleep(2)
