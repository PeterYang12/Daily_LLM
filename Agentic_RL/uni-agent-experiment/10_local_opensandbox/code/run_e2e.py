"""Reuse Uni-Agent ReAct/Task/Gateway/SWE grading with the OpenSandbox provider."""
import argparse,asyncio,json,sys
from pathlib import Path
sys.path.insert(0,'/lab/scripts')
sys.path.insert(0,'/lab/scripts/swe_sandbox_profile')
from provider import OpenSandboxProvider
import run as harness
from profiling import Profile,install
from providers import PROFILES,save

async def main(args):
    install()
    original_save=Profile.save
    def save_profile(self):
        for event in self.events:
            if event.get('category')=='http_send' and str(event.get('target','')).startswith('e2b_'):
                event['target']='opensandbox'
        return original_save(self)
    Profile.save=save_profile
    original_task=harness.run_task
    async def run_task(**kwargs):
        cfg=kwargs['tools_kwargs']['task']
        cfg['sandbox']['provider']='opensandbox';cfg['sandbox']['runtime_timeout']=3600
        cfg['sandbox']['sandbox_kwargs'].update(cpu='4',memory='8Gi',shell_init='source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed')
        save(PROFILES[cfg['metadata']['_profile_key']].out/'input-config.json',cfg)
        return await original_task(**kwargs)
    harness.run_task=run_task
    task=json.loads((args.root/'manifest.json').read_text())['tasks'][0]
    # The reused case driver has one image-reference slot for remote providers;
    # OpenSandbox consumes an OCI image directly, rather than an E2B template ID.
    images={task['instance_id']:{'build':{'template_id':task['image_digest']}}}
    args.tag='controls' if args.mode=='controls' else 'react'
    actor=None
    if args.mode=='react':
        base=args.root/'controls/opensandbox'/task['instance_id']
        b=json.loads((base/'baseline/result.json').read_text());g=json.loads((base/'oracle/result.json').read_text())
        assert b['eval_completed'] and not b['resolved'] and g['eval_completed'] and g['resolved']
        from transformers import AutoTokenizer
        from uni_agent.gateway.config import GatewayActorConfig
        from uni_agent.gateway.gateway import _GatewayActor
        tokenizer=harness.TemplateResultTokenIdsWrapper(AutoTokenizer.from_pretrained('/lab/models/'+harness.MODEL))
        actor=_GatewayActor(GatewayActorConfig(tokenizer=tokenizer,tool_parser_name='qwen3_coder',rollout_backend='vllm',prompt_length=122880,response_length=8192,allowed_request_sampling_param_keys=frozenset({'stop'})),harness.TimedBackend(backend_base_url=args.base_url,backend_model=harness.MODEL,timeout=240))
        await actor.start()
    results=[]
    try:
        for mode in (['baseline','oracle'] if args.mode=='controls' else ['react']):
            results.append(await harness.case(task,'opensandbox',mode,0,args,actor,images))
    finally:
        if actor:await actor.shutdown()
    save(args.root/(args.tag+'-summary.json'),{'results':results})
    assert all(r.get('eval_completed') and not r.get('error') for r in results),results
    if args.mode=='react':assert all(r.get('trajectory_valid') for r in results)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--mode',choices=['controls','react'],required=True);p.add_argument('--base-url',default='http://172.30.90.9:8000/v1')
    asyncio.run(main(p.parse_args()))
