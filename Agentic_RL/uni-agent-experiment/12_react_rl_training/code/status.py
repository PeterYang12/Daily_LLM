"""Print a small, non-secret progress snapshot for the long RL run."""
import json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
rows=[]
path=ROOT/'results/main-metrics.jsonl'
if path.exists():
    for line in path.read_text().splitlines():
        try:rows.append(json.loads(line))
        except json.JSONDecodeError:pass
train=[r for r in rows if r['step']>0]
latest=train[-1] if train else {'step':0,'data':{}}
data=latest['data']
result={'step':latest['step'],'target_steps':300,'completed_updates_in_log':len(train),
        'reward_mean':data.get('critic/rewards/mean'),'grad_norm':data.get('actor/grad_norm'),
        'last_step_s':data.get('timing_s/step'),'last_rollout_wait_s':data.get('timing_s/gen'),
        'last_actor_update_s':data.get('timing_s/update_actor'),
        'sampled_parameter_change_fraction':data.get('weights/sample_changed_fraction'),
        'metrics_age_s':round(time.time()-path.stat().st_mtime,1) if path.exists() else None,
        'checkpoint_directories':[p.name for p in (ROOT/'checkpoints').glob('global_step_*') if p.is_dir()]}
exit_file=ROOT/'results/main-exit.json'
result['exit']=json.loads(exit_file.read_text()) if exit_file.exists() else None
telemetry=ROOT/'results/main/telemetry.jsonl'
if telemetry.exists():
    with telemetry.open('rb') as f:
        f.seek(max(0,telemetry.stat().st_size-65536));lines=f.read().splitlines()
    for line in reversed(lines):
        try:sample=json.loads(line);break
        except json.JSONDecodeError:continue
    result['active_rollout_sandboxes']=sample.get('rollout_sandbox_count')
    result['gpu_use_pct']=[v.get('GPU use (%)') for k,v in sample.get('gpu',{}).items() if k.startswith('card')]
print(json.dumps(result,indent=2))
