"""Reuse the existing Uni-Agent SWE task and E2B provider against this local service."""
import argparse,asyncio,json,os,sys
from pathlib import Path
sys.path.insert(0,'/lab/scripts')
sys.path.insert(0,'/lab/scripts/swe_sandbox_profile')
import run as runner
import prepare_templates
from run_e2b_e2e import load_environment
from providers import PROFILES,save

original_run_task=runner.run_task
async def local_run_task(**kwargs):
    cfg=kwargs['tools_kwargs']['task']
    # The seeded local team allows one-hour sandboxes; the previous remote study
    # requested 90 minutes. Preserve execution semantics and use the local quota.
    cfg['sandbox']['runtime_timeout']=min(cfg['sandbox']['runtime_timeout'],3600)
    save(PROFILES[cfg['metadata']['_profile_key']].out/'input-config.json',cfg)
    return await original_run_task(**kwargs)

def local_environment(_ignored=None):
    load_environment('/lab/secrets/local-e2b/container.env')
    assert os.environ['E2B_API_URL']=='http://172.30.90.1:18090','Refusing a different E2B service'

def local_qualified(root,tag,tasks):
    """This is a local-service acceptance test; qualify its own E2B baseline/gold."""
    good=[]
    for task in tasks:
        base=root/tag/'e2b'/task['instance_id']
        baseline=json.loads((base/'baseline/result.json').read_text())
        gold=json.loads((base/'oracle/result.json').read_text())
        if baseline.get('eval_completed') and not baseline.get('resolved') and gold.get('eval_completed') and gold.get('resolved') and not baseline.get('error') and not gold.get('error'):
            good.append(task)
    return good

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path)
    p.add_argument('--mode',choices=['templates','controls','react'],required=True)
    p.add_argument('--base-url',default='http://172.30.90.10:8000/v1')
    a=p.parse_args()
    runner.load_environment=local_environment;runner.qualified=local_qualified;runner.run_task=local_run_task
    prepare_templates.load_environment=local_environment
    if a.mode=='templates':asyncio.run(prepare_templates.main(a.root))
    else:
        a.tag='controls-v2' if a.mode=='controls' else 'react-local'
        a.controls_tag='controls-v2';a.concurrency=1;a.instances=None;a.providers='e2b';a.repeats=1
        asyncio.run(runner.main(a))
