"""Matched ReAct SWE-bench rollouts and profiles on Docker and native E2B templates."""
import argparse,asyncio,copy,dataclasses,hashlib,json,math,os,re,sys,time,uuid
from pathlib import Path
sys.path.insert(0,'/lab/scripts')
import httpx,yaml
from uni_agent.tasks.registry import register_task
from uni_agent.tasks.base import TaskResult
from uni_agent.tasks.swe_bench.task import SWEBenchTask
from uni_agent.tasks.swe_bench.reward import compute_reward
from uni_agent.framework.task_runner import run_task
from uni_agent.gateway.session import SessionHandle
from uni_agent.logging import sample_logging
from examples.gateway.debug_launcher import OpenAICompletionsBackend,TemplateResultTokenIdsWrapper,write_trajectories_jsonl,capture_debug_snapshot,write_debug_snapshot_json
from run_e2b_e2e import load_environment
from run_swe import source_patch,is_test
from profiling import CURRENT,SESSIONS,Profile,install
from providers import PROFILES,checked,prepare,save

MODEL='Qwen3-Coder-30B-A3B-Instruct'

async def timed_reward(meta,sandbox,profile,timeout):
    """Measure verifier body wall time inside the sandbox, strip the marker.

    Bash EPOCHREALTIME adds no external timer process. The difference from client
    wall time includes transport, dispatch and output handling, not just wire RTT.
    """
    original=sandbox.exec_shell
    async def wrapped(script,**kwargs):
        marker='__UA_VERIFIER_TIME_'+uuid.uuid4().hex
        measured=('__ua_prof_start=$EPOCHREALTIME\n'+script+'\n__ua_prof_code=$?\n'
                  +f"printf '\\n{marker} %s %s\\n' \"$__ua_prof_start\" \"$EPOCHREALTIME\" >&2\n"
                  +'exit "$__ua_prof_code"')
        with profile.span('verifier_exec') as event:
            result=await original(measured,**kwargs)
            pattern=r'\n'+re.escape(marker)+r' ([0-9.]+) ([0-9.]+)\n?'
            match=re.search(pattern,result.stderr)
            if match:
                event['body_seconds']=float(match.group(2))-float(match.group(1))
                result.stderr=re.sub(pattern,'',result.stderr)
            return result
    sandbox.exec_shell=wrapped
    try:return await compute_reward(meta,sandbox,eval_timeout=timeout)
    finally:sandbox.exec_shell=original

@register_task('profile_swe_bench')
class ProfileSWETask(SWEBenchTask):
    async def run(self):
        cfg=self.config;meta=cfg.metadata;p=PROFILES[meta['_profile_key']]
        mode=meta['_mode'];out=p.out;info={'instance_id':meta['instance_id'],'provider':meta['_provider'],'mode':mode}
        sandbox=self.build_sandbox();alive=False;agent_result=None;score=None;cleanup=[]
        try:
            with p.phase('provision'):
                await sandbox.__aenter__();alive=True
                initial_tree=await prepare(sandbox,p,meta['_tmux_archive'],out/'agent-environment',meta['base_commit'],tools=mode=='react')
            if mode in {'baseline','oracle'}:
                if mode=='oracle':
                    with p.phase('oracle_patch'):
                        await sandbox.write_file('/tmp/official-gold.patch',meta['patch'])
                        await checked(sandbox,['git','apply','--whitespace=fix','/tmp/official-gold.patch'],workdir='/testbed')
                with p.phase('verify'):
                    score=await timed_reward(meta,sandbox,p,cfg.eval_timeout)
                info['finished']=True
            else:
                with p.phase('agent'),p.span('agent_total'):
                    agent_result=await asyncio.wait_for(self.build_agent().run(sandbox=sandbox,messages=cfg.prompt,workdir='/testbed'),timeout=1800)
                save(out/'agent-result.json',dataclasses.asdict(agent_result))
                info['finished']=agent_result.finished;info['agent_info']=agent_result.info
                with p.phase('capture_patch'):
                    await checked(sandbox,['git','add','-A'],workdir='/testbed')
                    patch=(await checked(sandbox,['git','diff','--cached','--binary',initial_tree],workdir='/testbed')).stdout
                    names=(await checked(sandbox,['git','diff','--cached','--name-status',initial_tree],workdir='/testbed')).stdout
                    filtered,excluded=source_patch(patch)
                    (out/'candidate-delta.patch').write_text(patch)
                    (out/'candidate-source-only.patch').write_text(filtered)
                    (out/'changed-files.txt').write_text(names)
                    info.update(patch_bytes=len(patch.encode()),source_patch_bytes=len(filtered.encode()),excluded_test_paths=excluded)
                    info['existing_tests_modified']=[line.split('\t')[-1] for line in names.splitlines() if not line.startswith('A\t') and is_test(line.split('\t')[-1])]
                with p.phase('agent_cleanup'):
                    await sandbox.stop();alive=False;cleanup.append('agent')
                sandbox=self.build_sandbox()
                with p.phase('verifier_provision'):
                    await sandbox.__aenter__();alive=True
                    verifier_tree=await prepare(sandbox,p,meta['_tmux_archive'],out/'verifier-environment',meta['base_commit'],tools=False)
                    if verifier_tree!=initial_tree:raise RuntimeError('Fresh verifier image tree differs from agent initial tree')
                    if filtered:
                        await sandbox.write_file('/tmp/candidate-source-only.patch',filtered)
                        await checked(sandbox,['git','apply','--whitespace=fix','/tmp/candidate-source-only.patch'],workdir='/testbed')
                with p.phase('verify'):
                    score=await timed_reward(meta,sandbox,p,cfg.eval_timeout)
            save(out/'verifier.json',score);info.update(score)
        except BaseException as exc:
            info['error']=f'{type(exc).__name__}: {exc}'
            raise
        finally:
            with p.phase('cleanup'):
                try:
                    await sandbox.stop();cleanup.append('final');info['cleanup_completed']=True
                except Exception as exc:info['cleanup_error']=str(exc)
            info['cleanup_steps']=cleanup
            save(out/'task-detail.json',info)
        return TaskResult(reward=float(score['resolved']),accuracy=float(score['resolved']),
                          finished=info['finished'],extra_info=info)

class TimedBackend(OpenAICompletionsBackend):
    async def generate(self,request_id,*,prompt_ids,sampling_params,**kwargs):
        p=CURRENT.get()
        if p is None:return await super().generate(request_id,prompt_ids=prompt_ids,sampling_params=sampling_params,**kwargs)
        with p.span('model_api',prompt_tokens=len(prompt_ids),max_tokens=sampling_params.get('max_tokens')) as event:
            result=await super().generate(request_id,prompt_ids=prompt_ids,sampling_params=sampling_params,**kwargs)
            event.update(completion_tokens=len(result.token_ids),stop_reason=result.stop_reason,
                         logprobs_present=result.log_probs is not None)
            return result

async def metrics(base_url):
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r=await c.get(base_url.rstrip('/').removesuffix('/v1')+'/metrics');r.raise_for_status();return r.text
    except Exception as exc:return '# metrics unavailable: '+str(exc)

def audit(trajectories):
    rows=[]
    for t in trajectories:
        ok=bool(t.response_ids) and len(t.response_ids)==len(t.response_mask) and all(m in [0,1] for m in t.response_mask)
        lp=t.response_logprobs
        ok=ok and lp is not None and len(lp)==len(t.response_ids) and all(isinstance(x,(int,float)) and math.isfinite(x) for x in lp)
        rows.append({'valid':ok,'generated_tokens':sum(t.response_mask),'context_tokens':len(t.response_mask)-sum(t.response_mask),'num_turns':t.num_turns})
    return {'valid':bool(rows) and all(x['valid'] for x in rows),'rows':rows,'trajectory_count':len(rows)}

def sum_category(events,category,phase=None):
    return sum(e.get('duration_ns',0)/1e9 for e in events if e['category']==category and (phase is None or e['phase']==phase))

async def case(task,provider,mode,slot,args,actor,templates):
    iid=task['instance_id'];trial=task.get('trial_id',iid);out=args.root/args.tag/provider/trial
    if args.mode=='controls':out=out/mode
    if (out/'result.json').exists():return json.loads((out/'result.json').read_text())
    out.mkdir(parents=True,exist_ok=False)
    key=f'{args.tag}:{provider}:{trial}:{mode}';profile=Profile(key,out)
    PROFILES[key]=profile;session_id='profile-'+uuid.uuid4().hex[:16];session_open=False
    result={'instance_id':iid,'trial_id':trial,'provider':provider,'mode':mode,'tag':args.tag,'output':str(out),'started_unix':time.time(),'slot':slot,'base_url':args.base_url}
    task_result=None;trajectories=[]
    start=time.monotonic()
    before=await metrics(args.base_url);(out/'metrics-before.txt').write_text(before)
    try:
        with profile.active():
            if actor:
                with profile.phase('session_setup'):
                    SESSIONS[session_id]=profile
                    handle=await actor.create_session(session_id,metadata={'instance_id':iid,'provider':provider},
                        sampling_params={'temperature':0.2,'top_p':0.9,'max_tokens':4096,'logprobs':True})
                    session_open=True
            else:handle=SessionHandle(session_id=session_id,base_url='http://unused.invalid/v1')
            cfg=yaml.safe_load(Path('/lab/configs/swe-react-128k.yaml').read_text())
            cfg['name']='profile_swe_bench';cfg['metadata']=copy.deepcopy(task['row']['extra_info']['tools_kwargs']['task']['metadata'])
            cfg['metadata'].update(_profile_key=key,_mode=mode,_provider=provider,_tmux_archive=str(args.root/'assets/tmux.tar.gz'))
            image=task['image_digest'] if provider=='docker' else templates[iid]['build']['template_id']
            cfg['sandbox']={'provider':'profile_'+provider,'image':image,'runtime_timeout':5400,
                            'sandbox_kwargs':{'profile_key':key,'instance_label':args.root.name,'slot':slot}}
            save(out/'input-config.json',cfg)
            async with sample_logging(session_id,log_path=str(out/'task.log')):
                task_result=await run_task(session=handle,tools_kwargs={'task':cfg},raw_prompt=task['row']['prompt'],sample_index=0,model_name=MODEL)
            save(out/'task-result.json',dataclasses.asdict(task_result))
            result.update(task_result.extra_info)
            result['reward']=task_result.reward
    except Exception as exc:
        result['error']=f'{type(exc).__name__}: {exc}'
    finally:
        if session_open:
            with profile.active(),profile.phase('trajectory_export'):
                try:
                    write_debug_snapshot_json(output_dir=out/'sessions',session_id=session_id,snapshot=capture_debug_snapshot(actor,session_id,{}))
                    trajectories=await actor.finalize_session(session_id);session_open=False
                    for t in trajectories:
                        if task_result is not None:
                            t.reward_score=task_result.reward;t.finished=task_result.finished
                            t.reward_metrics={'accuracy':task_result.accuracy}
                    write_trajectories_jsonl(output_dir=out/'sessions',session_id=session_id,trajectories=trajectories,
                        metadata={'instance_id':iid,'provider':provider,'reward_source':'ProfileSWETask; no training update'})
                    save(out/'trajectory-audit.json',audit(trajectories));result['trajectory_valid']=audit(trajectories)['valid']
                except Exception as exc:result['trajectory_error']=str(exc)
                if session_open:await actor.abort_session(session_id)
            SESSIONS.pop(session_id,None)
        (out/'metrics-after.txt').write_text(await metrics(args.base_url))
        result['wall_seconds']=time.monotonic()-start;result['ended_unix']=time.time()
        result['timings_s']={e['name']:e['duration_ns']/1e9 for e in profile.events if e['category']=='phase'}
        result['agent_breakdown_s']={name:sum_category(profile.events,name,'agent') for name in ['model_roundtrip','model_api','gateway_http','codec','tool_call','tool_init','tool_close']}
        result['client_operations']={name:sum(e['category']==name for e in profile.events) for name in ['http_send','docker_cli','sandbox_exec','file_read','file_write','tool_call','model_api']}
        result['model_tokens']={'prompt':sum(e.get('prompt_tokens',0) or 0 for e in profile.events if e['category']=='model_api'),
                                'generated':sum(e.get('completion_tokens',0) or 0 for e in profile.events if e['category']=='model_api')}
        profile.save();save(out/'result.json',result)
        PROFILES.pop(key,None)
        print(json.dumps({k:result.get(k) for k in ['tag','provider','instance_id','mode','resolved','eval_completed','finished','wall_seconds','error','trajectory_error']}),flush=True)
    return result

def qualified(root,tag,tasks):
    good=[]
    index=root/(tag+'-index.json')
    sources=json.loads(index.read_text()).get('sources',{}) if index.exists() else {}
    for task in tasks:
        valid=True
        source_tag=sources.get(task['instance_id'],tag)
        for provider in ['docker','e2b']:
            path=root/source_tag/provider/task['instance_id']
            try:b=json.loads((path/'baseline/result.json').read_text());g=json.loads((path/'oracle/result.json').read_text())
            except FileNotFoundError:valid=False;continue
            valid=valid and b.get('eval_completed') and not b.get('resolved') and g.get('eval_completed') and g.get('resolved') and 'error' not in b and 'error' not in g
        if valid:
            states=[json.loads((root/source_tag/provider/task['instance_id']/'baseline/agent-environment/initial-state.json').read_text()) for provider in ['docker','e2b']]
            valid=states[0]['tree']==states[1]['tree'] and states[0]['head']==states[1]['head']
        if valid:good.append(task)
    return good

async def main(args):
    load_environment('/lab/secrets/e2b.env');install()
    manifest=json.loads((args.root/'manifest.json').read_text());tasks=manifest['tasks']
    if args.instances:tasks=[t for t in tasks if t['instance_id'] in args.instances.split(',')]
    templates={r['instance_id']:r for r in json.loads((args.root/'templates.json').read_text()) if r.get('status')=='ready'}
    if args.mode=='react':
        tasks=qualified(args.root,args.controls_tag,tasks)
        save(args.root/(args.tag+'-qualified.json'),{'n':len(tasks),'instances':[t['instance_id'] for t in tasks]})
    if not tasks:raise RuntimeError('No tasks selected / qualified')
    if args.repeats>1:
        tasks=[{**task,'trial_id':task['instance_id']+f'__repeat{repeat}'} for repeat in range(args.repeats) for task in tasks]
    actor=None
    if args.mode=='react':
        from transformers import AutoTokenizer
        from uni_agent.gateway.config import GatewayActorConfig
        from uni_agent.gateway.gateway import _GatewayActor
        tokenizer=TemplateResultTokenIdsWrapper(AutoTokenizer.from_pretrained('/lab/models/'+MODEL))
        actor=_GatewayActor(GatewayActorConfig(tokenizer=tokenizer,tool_parser_name='qwen3_coder',rollout_backend='vllm',
            prompt_length=122880,response_length=8192,allowed_request_sampling_param_keys=frozenset({'stop'})),
            TimedBackend(backend_base_url=args.base_url,backend_model=MODEL,timeout=240))
        await actor.start()
    jobs=[]
    for index,task in enumerate(tasks):
        order=['docker','e2b'] if index%2==0 else ['e2b','docker']
        for provider in order:
            if provider not in args.providers.split(','):continue
            if provider=='e2b' and task['instance_id'] not in templates:raise RuntimeError('Missing ready template: '+task['instance_id'])
            for mode in (['baseline','oracle'] if args.mode=='controls' else ['react']):jobs.append((task,provider,mode))
    slots=asyncio.Queue()
    for n in range(args.concurrency):slots.put_nowait(n)
    results=[]
    async def worker(job):
        slot=await slots.get()
        try:
            result=await case(*job,slot,args,actor,templates);results.append(result)
            save(args.root/(args.tag+'-progress.json'),{'completed':len(results),'total':len(jobs),'results':results})
        finally:slots.put_nowait(slot)
    started=time.monotonic()
    try:await asyncio.gather(*(worker(job) for job in jobs))
    finally:
        if actor:await actor.shutdown()
    save(args.root/(args.tag+'-summary.json'),{'tag':args.tag,'mode':args.mode,'concurrency':args.concurrency,
        'wall_seconds':time.monotonic()-started,'results':results,'planned_tasks':len(tasks),'jobs':len(jobs)})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--mode',choices=['controls','react'],required=True)
    p.add_argument('--tag',required=True);p.add_argument('--controls-tag',default='controls');p.add_argument('--concurrency',type=int,default=1)
    p.add_argument('--instances');p.add_argument('--providers',default='docker,e2b');p.add_argument('--base-url',default='http://172.30.90.5:8000/v1');p.add_argument('--repeats',type=int,default=1)
    asyncio.run(main(p.parse_args()))
