"""Build a 4 GiB base template for the ordinary-page capacity check."""
import json
from pathlib import Path
import time

from e2b import Template

ROOT = Path('/data2/yuhanya/e2b-local-20260916')
cfg = json.loads(Path('/home/yuhanya/.config/e2b-local-20260916/client.json').read_text())
assert cfg['api_url'] == 'http://10.145.64.67:8088'
began = time.monotonic()
info = Template.build(
    Template().from_template('base'),
    'base-4k-4g',
    cpu_count=2,
    memory_mb=4096,
    api_key=cfg['api_key'],
    api_url=cfg['api_url'],
    request_timeout=60,
)
record = {
    'alias': 'base-4k-4g',
    'template_id': info.template_id,
    'build_id': info.build_id,
    'memory_mb': 4096,
    'cpu_count': 2,
    'wall_seconds': time.monotonic() - began,
    'status': 'ready',
}
(ROOT / 'migrations/20260920-4k-512/after/capacity-template.json').write_text(
    json.dumps(record, indent=2) + '\n')
print(json.dumps(record), flush=True)
