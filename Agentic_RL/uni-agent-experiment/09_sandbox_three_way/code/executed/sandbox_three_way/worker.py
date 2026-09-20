"""Run the unchanged profiled SWE task/provider against one deployment."""
import argparse,asyncio,json,os,sys
from pathlib import Path
sys.path.insert(0,'/lab/scripts')
sys.path.insert(0,'/lab/scripts/swe_sandbox_profile')
import run as runner
import microbench,native_shell_probe
from providers import PROFILES,save
from profiling import install
from run_e2b_e2e import load_environment

def main(args):
    provider='docker' if args.variant=='docker' else 'e2b'
    def environment(_ignored=None):
        load_environment('/lab/secrets/e2b.env' if args.variant=='remote_e2b' else '/lab/secrets/local-e2b/container.env')
        if args.variant=='local_e2b':assert os.environ['E2B_API_URL']=='http://172.30.90.1:18090'
        if args.variant=='remote_e2b':assert '172.30.90.1' not in os.environ['E2B_API_URL']
    def qualified(root,tag,tasks):
        good=[]
        for task in tasks:
            p=root/tag/provider/task['instance_id']
            b=json.loads((p/'baseline/result.json').read_text());g=json.loads((p/'oracle/result.json').read_text())
            if b.get('eval_completed') and not b.get('resolved') and g.get('eval_completed') and g.get('resolved') and not b.get('error') and not g.get('error'):good.append(task)
        return good
    original=runner.run_task
    async def task_with_common_limits(**kwargs):
        cfg=kwargs['tools_kwargs']['task'];cfg['sandbox']['runtime_timeout']=3600
        save(PROFILES[cfg['metadata']['_profile_key']].out/'input-config.json',cfg)
        return await original(**kwargs)
    if args.mode=='micro':
        environment();install()
        task=json.loads((args.root/'manifest.json').read_text())['tasks'][0]
        template=json.loads((args.root/'templates.json').read_text())[0]['build']['template_id']
        asyncio.run(microbench.run(args.root,provider,task,template));return
    if args.mode=='native':
        native_shell_probe.load_environment=environment;args.wait=False
        asyncio.run(native_shell_probe.main(args));return
    runner.load_environment=environment;runner.qualified=qualified;runner.run_task=task_with_common_limits
    args.providers=provider;args.concurrency=1;args.instances=None;args.repeats=1;args.controls_tag='controls'
    asyncio.run(runner.main(args))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path)
    p.add_argument('--variant',choices=['docker','local_e2b','remote_e2b'],required=True)
    p.add_argument('--mode',choices=['micro','native','controls','react'],required=True)
    p.add_argument('--tag',default='controls');p.add_argument('--base-url',default='http://172.30.90.9:8000/v1')
    main(p.parse_args())
