"""Rebuild the previously validated SWE task image on the local E2B service."""
import json
from pathlib import Path
import time
from e2b import Template

root = Path('/data2/yuhanya/e2b-local-20260916')
config = json.loads(Path('/home/yuhanya/.config/e2b-local-20260916/client.json').read_text())
assert config['api_url'] == 'http://10.145.64.67:8088'
source = json.loads((root / 'configs/flask-source.json').read_text())
alias = 'local-swe-flask-5014'
def log(entry):
    message = str(entry).replace(config['api_key'], '<redacted>')
    with (root / 'logs/flask-template-build.jsonl').open('a') as out:
        out.write(json.dumps({'time': time.time(), 'message': message}) + '\n')
recipe = (Template().from_image(source['image_digest']).set_user('root').set_workdir('/testbed')
          .run_cmd('command -v tmux >/dev/null 2>&1 || (apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tmux && rm -rf /var/lib/apt/lists/*)'))
started = time.monotonic()
info = Template.build(recipe, alias, cpu_count=4, memory_mb=8192, on_build_logs=log,
                      api_key=config['api_key'], api_url=config['api_url'], request_timeout=60)
record = {**source, 'alias': alias, 'template_id': info.template_id,
          'build_id': info.build_id, 'cpu_count': 4, 'memory_mb': 8192,
          'wall_seconds': time.monotonic() - started, 'status': 'ready'}
(root / 'results/flask-template.json').write_text(json.dumps(record, indent=2))
(root / 'configs/template-map.json').write_text(json.dumps({'images': {source['image']: record}}, indent=2))
print(json.dumps(record))
