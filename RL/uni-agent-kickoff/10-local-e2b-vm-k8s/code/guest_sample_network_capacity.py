"""Bounded read-only ARP capacity sampling, run inside the E2B VM."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

print(json.dumps({'sampler_pid': os.getpid()}), flush=True)
for _ in range(300):
    lines = Path('/proc/net/stat/arp_cache').read_text().splitlines()
    columns = lines[0].split()
    rows = [dict(zip(columns, [int(v, 16) for v in row.split()])) for row in lines[1:]]
    print(json.dumps({
        'utc': datetime.now(timezone.utc).isoformat(),
        'entries': max(row['entries'] for row in rows),
        'table_fulls': sum(row.get('table_fulls', 0) for row in rows),
        'forced_gc_runs': sum(row.get('forced_gc_runs', 0) for row in rows),
        'gc_thresh3': int(Path('/proc/sys/net/ipv4/neigh/default/gc_thresh3').read_text()),
    }), flush=True)
    time.sleep(2)
