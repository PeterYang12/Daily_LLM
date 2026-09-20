"""Targeted gold-verifier diagnosis: shell wall, user CPU and system CPU time."""
import argparse,asyncio,json,re,time,uuid
from pathlib import Path
import run as runner
from profiling import install
from run_e2b_e2e import load_environment
from providers import save

async def resource_reward(meta,sandbox,profile,timeout):
    original=sandbox.exec_shell
    async def measured(script,**kwargs):
        marker='__UA_RESOURCE_'+uuid.uuid4().hex
        command=f"TIMEFORMAT='{marker} %R %U %S'\ntime {{\n{script}\n}}"
        with profile.span('verifier_resources') as event:
            result=await original(command,**kwargs)
            pattern=re.escape(marker)+r' ([0-9.]+) ([0-9.]+) ([0-9.]+)\n?'
            match=re.search(pattern,result.stderr)
            if match:
                wall,user,system=map(float,match.groups())
                event.update(body_wall_s=wall,user_cpu_s=user,system_cpu_s=system,
                             average_cpu_cores=(user+system)/wall if wall else 0)
                result.stderr=re.sub(pattern,'',result.stderr)
            return result
    sandbox.exec_shell=measured
    try:return await runner.compute_reward(meta,sandbox,eval_timeout=timeout)
    finally:sandbox.exec_shell=original

async def main(args):
    if args.wait:
        while not (args.root/'NATIVE_PROBE_COMPLETE').exists():await asyncio.sleep(5)
    load_environment('/lab/secrets/e2b.env');install();runner.timed_reward=resource_reward
    tasks={t['instance_id']:t for t in json.loads((args.root/'manifest.json').read_text())['tasks']}
    templates={t['instance_id']:t for t in json.loads((args.root/'templates.json').read_text())}
    results=[]
    for repeat in range(2):
        for iid in ['django__django-14373','pylint-dev__pylint-7080','psf__requests-1921']:
            for provider in (['docker','e2b'] if repeat==0 else ['e2b','docker']):
                args.tag=f'verifier-cpu-r{repeat}';args.mode='controls'
                path=args.root/args.tag/provider/iid/'oracle'
                result=await runner.case(tasks[iid],provider,'oracle',0,args,None,templates)
                events=json.loads((path/'spans.json').read_text())['events']
                metrics=[{k:v for k,v in e.items() if k in ['body_wall_s','user_cpu_s','system_cpu_s','average_cpu_cores']} for e in events if e['category']=='verifier_resources']
                results.append({'repeat':repeat,'provider':provider,'instance_id':iid,'resolved':result.get('resolved'),'resources':metrics,'verifier_wall_s':result.get('timings_s',{}).get('verify')})
                save(args.root/'verifier-cpu-probe.json',results)
                print(json.dumps(results[-1]),flush=True)
    (args.root/'VERIFIER_PROBE_COMPLETE').write_text(str(time.time())+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--wait',action='store_true')
    p.add_argument('--base-url',default='http://172.30.90.9:8000/v1');asyncio.run(main(p.parse_args()))
