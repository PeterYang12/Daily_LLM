"""Read this run's status without contacting or changing the trainer."""
import json
from pathlib import Path
from runtime_common import ROOT


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def tail_json(path):
    try:
        with path.open('rb') as f:
            f.seek(max(0, path.stat().st_size-131072))
            lines = f.read().splitlines()
        return json.loads(lines[-1]) if lines else None
    except (OSError, json.JSONDecodeError):
        return None


state = read_json(ROOT/'state.json', {})
launch = read_json(ROOT/'results/launch.json', {})
telemetry = tail_json(ROOT/'results/telemetry.jsonl') or {}
metrics = tail_json(ROOT/'results/train-metrics.jsonl')
result = {'state': state.get('status'), 'attempt': launch.get('attempt'),
          'training_pid': launch.get('training_pid'), 'log': str(ROOT/launch.get('log', 'logs')),
          'active_training_sandboxes': telemetry.get('active_training_sandboxes'),
          'latest_metrics': metrics, 'process_result': read_json(ROOT/'results/main-exit.json')}
optimizer = {}
for path in (ROOT/'results/optimizer').glob('rank-*.jsonl'):
    row = tail_json(path)
    if row:
        optimizer[path.stem] = {k: row.get(k) for k in ['optimizer_update', 'successful', 'grad_norm', 'sampled_changed']}
result['optimizer'] = optimizer
print(json.dumps(result, indent=2))
