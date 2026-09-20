"""Confirm the final step and evaluation reached W&B's remote API."""
import json,time
from pathlib import Path
import wandb
ROOT=Path('/lab/rl-swe-e2b-20260916')
launch=json.loads((ROOT/'results/main-launch.json').read_text())
summary=json.loads((ROOT/'results/training-summary.json').read_text())
run_id=launch['wandb_url'].rsplit('/',1)[1]
key='val-core/princeton-nlp/SWE-bench_Verified/acc/mean@1'
expected=summary['validation'][-1]['accuracy']
result={}
for _ in range(24):
    api=wandb.Api(timeout=30)
    run=api.run('yuhanya-amd/uni-agent-swe-rl-rocm/'+run_id)
    data=dict(run.summary)
    result={'url':launch['wandb_url'],'state':run.state,'last_step':data.get('_step'),
            'final_validation':data.get(key),'expected_validation':expected,'checked_unix':time.time()}
    result['success']=result['last_step']==300 and result['final_validation']==expected
    if result['success']:break
    time.sleep(5)
(ROOT/'results/wandb-verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result));assert result['success']
