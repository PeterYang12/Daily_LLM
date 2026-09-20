import json
from pathlib import Path
import numpy as np
from runtime_common import ROOT, save

start = json.loads((ROOT/'results/launch.json').read_text())['started_unix']
locations = {p.parent.name: p for p in (ROOT/'results/trajectories').rglob('trajectory.json')}
rows = []
errors = []
pending = []
for path in (ROOT/'results/sessions').glob('*/result.json'):
    task = json.loads(path.read_text())
    if task.get('started_unix', 0) < start:
        continue
    sid = task['session_id']
    meta_path = locations.get(sid)
    if meta_path is None or not meta_path.with_suffix('.npz').exists():
        pending.append(sid)
        continue
    meta = json.loads(meta_path.read_text())
    try:
        with np.load(meta_path.with_suffix('.npz'), allow_pickle=False) as arrays:
            for i, trajectory in enumerate(meta['trajectories']):
                prefix = f'traj{i}_'
                ids = arrays[prefix+'response_ids']
                mask = arrays[prefix+'response_mask']
                lp = arrays[prefix+'response_logprobs']
                valid = len(ids) == len(mask) == len(lp) and np.isfinite(lp).all() and np.isin(mask, [0,1]).all()
                reward_matches = float(trajectory['reward_score']) == float(task['reward'])
                row = {'session_id': sid, 'instance_id': task['instance_id'], 'trajectory': i,
                       'valid': bool(valid), 'reward_matches': reward_matches, 'response_tokens': len(ids),
                       'model_tokens': int(mask.sum()), 'reward': task['reward'], 'finished': trajectory['finished']}
                rows.append(row)
                if not valid or not reward_matches:
                    errors.append(row)
    except Exception as exc:
        errors.append({'session_id': sid, 'error': type(exc).__name__+': '+str(exc)})
save(ROOT/'results/trajectory-audit.json', {'rows': rows, 'errors': errors, 'pending': pending})
print(json.dumps({'trajectories': len(rows), 'errors': len(errors), 'pending': len(pending)}))
if errors:
    print(json.dumps(errors[:3]))
    raise SystemExit(1)
