"""Matched command/file microbenchmarks; separate from sampled task scores."""
import argparse,asyncio,json,os,statistics,sys,time
from pathlib import Path
sys.path.insert(0,'/lab/scripts')
from uni_agent.sandbox import SandboxConfig,build_sandbox
from uni_agent.tools import Toolbox
from run_e2b_e2e import load_environment
from profiling import Profile,install
from providers import PROFILES,checked,prepare,save

async def measure(p,name,count,fn):
    times=[];samples=[]
    for i in range(count):
        start=time.perf_counter();n=len(p.events);value=await fn(i)
        elapsed=time.perf_counter()-start;times.append(elapsed)
        samples.append({'elapsed_s':elapsed,'http_calls':sum(e['category']=='http_send' for e in p.events[n:]),
                        'docker_cli_calls':sum(e['category']=='docker_cli' for e in p.events[n:]),'result':value})
    ordered=sorted(times)
    return {'operation':name,'n':count,'median_s':statistics.median(times),'mean_s':statistics.mean(times),
            'p95_s':ordered[min(len(ordered)-1,int(.95*len(ordered)))],'min_s':min(times),'max_s':max(times),'samples':samples}

async def run(root,provider,task,template):
    out=root/'microbench'/provider;out.mkdir(parents=True,exist_ok=False);p=Profile('microbench:'+provider,out);PROFILES[p.key]=p
    config=SandboxConfig(provider='profile_'+provider,image=task['image_digest'] if provider=='docker' else template,
        runtime_timeout=1800,sandbox_kwargs={'profile_key':p.key,'instance_label':root.name,'slot':0})
    results=[]
    with p.active(),p.phase('microbench'):
        async with build_sandbox(config) as sb:
            await prepare(sb,p,root/'assets/tmux.tar.gz',out/'environment',task['base_commit'],tools=True)
            await checked(sb,['true'])
            async def command(i):
                r=await checked(sb,['true']);return {'exit_code':r.exit_code}
            results.append(await measure(p,'exec_true',30,command))
            async def sleeper(i):
                r=await checked(sb,['/opt/miniconda3/envs/testbed/bin/python','-c','import time; t=time.perf_counter(); time.sleep(.1); print(time.perf_counter()-t)'])
                return {'inner_sleep_s':float(r.stdout.strip())}
            results.append(await measure(p,'exec_python_sleep_100ms',10,sleeper))
            for size,count in [(1024,20),(1024*1024,5)]:
                payload=b'x'*size;path='/tmp/ua-profile-bytes'
                async def write(i):await sb.write_file(path,payload);return {'bytes':size}
                results.append(await measure(p,f'write_{size}',count,write))
                async def read(i):
                    value=await sb.read_file(path);assert value==payload;return {'bytes':len(value)}
                results.append(await measure(p,f'read_{size}',count,read))
            async with Toolbox.from_specs([{'name':'stateful_shell','command_timeout':30}],sandbox=sb) as toolbox:
                async def shell(i):
                    result=await toolbox.call('shell',{'command':'true'});assert result.status=='ok',result
                    return {'status':result.status}
                results.append(await measure(p,'stateful_shell_true',10,shell))
    p.save();save(out/'summary.json',results)
    print(json.dumps({'provider':provider,'results':[{k:v for k,v in r.items() if k!='samples'} for r in results]}),flush=True)

async def main(root):
    load_environment('/lab/secrets/e2b.env');install()
    task=next(t for t in json.loads((root/'manifest.json').read_text())['tasks'] if t['instance_id']=='pallets__flask-5014')
    template=next(t['build']['template_id'] for t in json.loads((root/'templates.json').read_text()) if t['instance_id']==task['instance_id'])
    for provider in ['docker','e2b']:await run(root,provider,task,template)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);asyncio.run(main(p.parse_args().root))
