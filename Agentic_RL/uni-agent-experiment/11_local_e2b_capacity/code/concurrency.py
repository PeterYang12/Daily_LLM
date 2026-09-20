"""Hold 24 real SWE sandboxes, then concurrently grade baseline/oracle controls.

Runs in ua-lab-cpu. Uses Uni-Agent's sandbox adapter and official SWE reward,
without a model, agent rollout, or parameter update. Creation is bounded to 3.
"""
import argparse,asyncio,json,os,sys,time
from pathlib import Path
sys.path[:0]=['/lab/scripts','/lab/scripts/swe_sandbox_profile']
from run_e2b_e2e import load_environment
from e2b import AsyncSandbox
from providers import ProfileE2B,checked,prepare,save
from profiling import Profile
from uni_agent.tasks.swe_bench.reward import compute_reward

async def main(args):
    load_environment('/lab/secrets/local-e2b/container.env')
    assert os.environ['E2B_API_URL']=='http://172.30.90.1:18090'
    args.output.mkdir(parents=True,exist_ok=False)
    task=json.loads((args.source/'manifest.json').read_text())['tasks'][0]
    meta=task['row']['extra_info']['tools_kwargs']['task']['metadata']
    template=json.loads((args.source/'templates.json').read_text())[0]
    rows=[];slots=asyncio.Semaphore(3);start=time.monotonic()
    report={'started_unix':time.time(),'n':args.n,'max_creates_in_flight':3,'template_id':template['build']['template_id'],
            'instance_id':meta['instance_id'],'cpu_per_sandbox':4,'memory_mib_per_sandbox':8192,
            'scope':'Concurrent sandbox and official verifier acceptance; no agent or model calls; no RL training'}
    for i in range(args.n):
        folder=args.output/f'case-{i:02d}';folder.mkdir()
        p=Profile(f'large-e2b-{i}',folder)
        sb=ProfileE2B(p,template['build']['template_id'],1800,args.output.name)
        rows.append({'index':i,'mode':'baseline' if i%2==0 else 'oracle','profile':p,'sandbox':sb})
    async def create(row):
        async with slots:
            t=time.monotonic()
            await row['sandbox'].start()
            row['create_s']=time.monotonic()-t
            row['sandbox_id']=row['sandbox'].sb.sandbox_id
    async def evaluate(row):
        p=row['profile'];sb=row['sandbox'];t=time.monotonic()
        try:
            with p.active():
                await prepare(sb,p,args.source/'assets/tmux.tar.gz',p.out/'environment',meta['base_commit'],tools=False)
                if row['mode']=='oracle':
                    await sb.write_file('/tmp/official-gold.patch',meta['patch'])
                    await checked(sb,['git','apply','--whitespace=fix','/tmp/official-gold.patch'],workdir='/testbed')
                row['verify_started_unix']=time.time()
                score=await compute_reward(meta,sb,eval_timeout=300)
                row['verify_finished_unix']=time.time()
                save(p.out/'verifier.json',score)
                row['resolved']=score['resolved'];row['eval_completed']=score['eval_completed']
                row['passed']=score['eval_completed'] and score['resolved']==(row['mode']=='oracle')
        except Exception as exc:
            row['error']=f'{type(exc).__name__}: {exc}';row['passed']=False
        row['workload_s']=time.monotonic()-t
    try:
        created=await asyncio.gather(*(create(r) for r in rows),return_exceptions=True)
        errors=[f'{type(x).__name__}: {x}' for x in created if isinstance(x,BaseException)]
        if errors:raise RuntimeError('; '.join(errors))
        report['create_batch_s']=time.monotonic()-start
        paginator=AsyncSandbox.list()
        live=[]
        while paginator.has_next:live.extend(await paginator.next_items())
        owned={r['sandbox_id'] for r in rows}
        report['simultaneously_live']=len(owned & {s.sandbox_id for s in live})
        report['all_running']=all(await asyncio.gather(*(r['sandbox'].is_alive() for r in rows)))
        save(args.output/'barrier.json',{**report,'sandbox_ids':sorted(owned)})
        # Let the host capture actual microVM/process/memory state at this barrier.
        deadline=time.monotonic()+90
        while not (args.output/'release').exists():
            if time.monotonic()>deadline:raise TimeoutError('Host did not capture/release concurrency barrier')
            await asyncio.sleep(.25)
        t=time.monotonic()
        await asyncio.gather(*(evaluate(r) for r in rows))
        report['workload_batch_s']=time.monotonic()-t
    except Exception as exc:
        report['error']=f'{type(exc).__name__}: {exc}'
    finally:
        async def cleanup(row):
            try:
                original=row['sandbox'].sb
                await row['sandbox'].stop()
                if original:
                    row['cleanup_verified']=not await original.is_running()
                else:row['cleanup_verified']=True
            except Exception as exc:row['cleanup_error']=str(exc)
            row['profile'].save()
        await asyncio.gather(*(cleanup(r) for r in rows))
        report['rows']=[{k:v for k,v in r.items() if k not in {'profile','sandbox'}} for r in rows]
        report['passed']=sum(bool(r.get('passed')) for r in rows)
        report['all_cleaned']=all(r.get('cleanup_verified') for r in rows)
        report['total_s']=time.monotonic()-start
        report['success']=report.get('simultaneously_live')==args.n and report.get('all_running') and report['passed']==args.n and report['all_cleaned'] and 'error' not in report
        save(args.output/'summary.json',report)
        print(json.dumps({k:v for k,v in report.items() if k!='rows'}),flush=True)
    assert report['success']

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--n',type=int,default=24);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--source',type=Path,default=Path('/lab/local-e2b-20260916/results/uni-agent-flask'))
    asyncio.run(main(p.parse_args()))
