"""Run upstream Uni-Agent agents with fresh SWE sandboxes and complete evidence."""
import argparse, asyncio, copy, dataclasses, hashlib, json, logging, re, time, traceback, os, shutil
from pathlib import Path
from datasets import load_dataset
from uni_agent.tasks import TaskConfigResolver, get_task
from uni_agent.tasks.swe_bench.reward import compute_reward
from uni_agent.logging import sample_logging
from lab_runtime import EvidenceDocker, checked, dump

MODEL='Qwen3-Coder-30B-A3B-Instruct'
HOST_CACHE='/home/yuhanya/uni-agent-lab/cache'

def sandbox(image,out,claude=False,mini=False,network='bridge'):
 args=['--cpus','2','--memory',os.environ.get('UA_SANDBOX_MEMORY','8g'),'--pids-limit','512','--security-opt','no-new-privileges','--cap-drop','ALL','--cap-add','CHOWN','--cap-add','DAC_OVERRIDE','--cap-add','FOWNER','--cap-add','SETGID','--cap-add','SETUID','--network',network,'--dns','10.145.80.1']
 args+=['--mount',f'type=bind,src={HOST_CACHE}/tmux,dst=/opt/ua-tmux,readonly','--mount',f'type=bind,src={HOST_CACHE}/tmux/tmux,dst=/usr/local/bin/tmux,readonly']
 if claude: args+=['--mount',f'type=bind,src={HOST_CACHE}/claude-2.1.236,dst=/usr/local/bin/claude,readonly']
 if mini: args+=['--mount',f'type=bind,src={HOST_CACHE}/mini,dst=/opt/mini-swe-agent,readonly']
 return EvidenceDocker(image=image,evidence_dir=out,run_args=args,pull_policy='never',start_timeout=90)

def is_test(path):
 return bool(re.search(r'(^|/)(tests?|testing)(/|$)|(^|/)(test_[^/]*|[^/]*_test)\.py$|(^|/)conftest\.py$',path))

def source_patch(patch):
 parts=re.split(r'(?=^diff --git )',patch,flags=re.M)
 kept=[]; excluded=[]
 for p in parts:
  if not p.strip(): continue
  m=re.search(r'^\+\+\+ b/(.+)$',p,re.M) or re.search(r'^--- a/(.+)$',p,re.M)
  path=m.group(1) if m else ''
  if is_test(path): excluded.append(path)
  else: kept.append(p)
 return ''.join(kept),excluded

async def evaluate(meta,image,patch,out,timeout,network):
 async with sandbox(image,out,network=network) as s:
  await checked(s,['git','config','--global','--add','safe.directory','/testbed'])
  await checked(s,['git','config','core.fileMode','false'],workdir='/testbed')
  if patch:
   await s.write_file('/tmp/candidate.patch',patch)
   await checked(s,['git','apply','--whitespace=fix','/tmp/candidate.patch'],workdir='/testbed')
  result=await compute_reward(meta,s,eval_timeout=timeout)
  dump(Path(out)/'verifier.json',result)
  return result

async def run_case(row,args,actor,sem):
 async with sem:
  cfg=copy.deepcopy(row['extra_info']['tools_kwargs']['task']); meta=cfg['metadata']; iid=meta['instance_id']; image=cfg['sandbox']['image']
  out=Path(args.output)/iid; out.mkdir(parents=True,exist_ok=False)
  start=time.time(); result={'instance_id':iid,'repo':meta['repo'],'mode':args.mode,'image':image,'started_unix':start,'finished':False,'resolved':False,'eval_completed':False}
  session=False
  try:
   async with sample_logging(iid,log_path=str(out/'task.log')):
    if args.mode in ['baseline','oracle']:
     patch=meta['patch'] if args.mode=='oracle' else ''
     result.update(await evaluate(meta,image,patch,out/'verifier',args.eval_timeout,args.network))
     result['finished']=True
    else:
     resolver=TaskConfigResolver.from_file(args.config)
     cfg['prompt']=row['prompt']
     runtime={'base_url':args.base_url,'model_name':MODEL,'api_key':'EMPTY'}
     if actor:
      handle=await actor.create_session(iid,metadata={'instance_id':iid,'mode':args.mode},sampling_params={'temperature':args.temperature,'top_p':0.9,'max_tokens':args.max_tokens})
      runtime['base_url']=handle.base_url; session=True
     resolved=resolver.resolve(cfg,runtime_model=runtime)
     task=get_task(resolved)
     dump(out/'task-config.json',task.config.model_dump(mode='json',exclude={'metadata'}))
     async with sandbox(image,out/'agent-sandbox',claude=args.mode=='claude',mini=args.mode=='mini',network=args.network) as s:
      await checked(s,['git','config','--global','--add','safe.directory','/testbed'])
      await checked(s,['git','config','core.fileMode','false'],workdir='/testbed')
      initial=await checked(s,['git','status','--porcelain'],workdir='/testbed')
      (out/'initial-git-status.txt').write_text(initial.stdout)
      # Only task/problem prompt is passed into the agent. Gold/test patches remain controller-side.
      agent_start=time.time()
      try:
       ar=await asyncio.wait_for(task.build_agent().run(sandbox=s,messages=task.config.prompt,workdir='/testbed'),timeout=args.agent_timeout)
       dump(out/'agent-result.json',dataclasses.asdict(ar)); result['finished']=ar.finished; result['agent_info']=ar.info
      except Exception as e:
       result['agent_error']=repr(e); (out/'agent-exception.txt').write_text(traceback.format_exc())
      result['agent_seconds']=time.time()-agent_start
      # Intent-to-add includes new source files; ignores gitignored runtime artifacts.
      await s.exec(['git','add','-N','--','.'],workdir='/testbed')
      patch=(await checked(s,['git','-c','core.fileMode=false','diff','--binary',meta['base_commit']],workdir='/testbed')).stdout
      (out/'candidate.patch').write_text(patch)
      status=await checked(s,['git','diff','--numstat',meta['base_commit']],workdir='/testbed'); (out/'diff-numstat.txt').write_text(status.stdout)
      for line in (out/'agent-sandbox'/'exec.jsonl').read_text().splitlines():
       r=json.loads(line)
       if r.get('argv',[None])[0]=='claude':
        (out/'claude.stdout').write_text(r.get('stdout','')); (out/'claude.stderr').write_text(r.get('stderr',''))
     source,excluded=source_patch(patch); (out/'candidate-source-only.patch').write_text(source)
     result['modified_test_paths']=excluded; result['patch_bytes']=len(patch.encode())
     result.update(await evaluate(meta,image,patch,out/'verifier',args.eval_timeout,args.network))
     if excluded:
      source_result=await evaluate(meta,image,source,out/'source-only-verifier',args.eval_timeout,args.network)
      result['source_only_resolved']=source_result['resolved']; result['source_only_eval_completed']=source_result['eval_completed']
     else:
      result['source_only_resolved']=result['resolved']; result['source_only_eval_completed']=result['eval_completed']
  except Exception as e:
   result['error']=repr(e); (out/'exception.txt').write_text(traceback.format_exc())
  finally:
   if session:
    from examples.gateway.debug_launcher import capture_debug_snapshot, write_trajectories_jsonl, write_debug_snapshot_json
    try:
     snapshot=capture_debug_snapshot(actor,iid,{})
     write_debug_snapshot_json(output_dir=Path(args.output)/'sessions',session_id=iid,snapshot=snapshot)
     trajectories=await actor.finalize_session(iid)
     write_trajectories_jsonl(output_dir=Path(args.output)/'sessions',session_id=iid,trajectories=trajectories,metadata={'instance_id':iid,'task_result_path':str(out/'result.json')})
     result['trajectory_count']=len(trajectories)
    except Exception as e: result['trajectory_error']=repr(e)
   result['wall_seconds']=time.time()-start
   dump(out/'result.json',result)
   print(json.dumps({k:result.get(k) for k in ['instance_id','resolved','eval_completed','finished','agent_seconds','wall_seconds','error','agent_error','trajectory_error']}),flush=True)
  return result

async def main(args):
 out=Path(args.output); out.mkdir(parents=True,exist_ok=False)
 dump(out/'run-config.json',{**vars(args),'sandbox_memory':os.environ.get('UA_SANDBOX_MEMORY','8g')})
 for src in [Path(__file__),Path(__file__).with_name('lab_runtime.py')]:
  (out/'runner-source').mkdir(exist_ok=True); shutil.copy2(src,out/'runner-source'/src.name)
 rows=load_dataset('parquet',data_files=args.data,split='train').to_list()
 if args.limit: rows=rows[:args.limit]
 actor=None
 if args.gateway and args.mode not in ['baseline','oracle']:
  from transformers import AutoTokenizer
  from uni_agent.gateway.gateway import _GatewayActor
  from uni_agent.gateway.config import GatewayActorConfig
  from examples.gateway.debug_launcher import OpenAICompletionsBackend,TemplateResultTokenIdsWrapper
  tokenizer=TemplateResultTokenIdsWrapper(AutoTokenizer.from_pretrained('/lab/models/'+MODEL))
  actor=_GatewayActor(GatewayActorConfig(tokenizer=tokenizer,tool_parser_name='qwen3_coder',rollout_backend='vllm',prompt_length=args.context_length-8192,response_length=8192,allowed_request_sampling_param_keys=frozenset({'stop'})),OpenAICompletionsBackend(backend_base_url=args.base_url,backend_model=MODEL,timeout=240))
  await actor.start()
 start=time.time()
 try:
  sem=asyncio.Semaphore(args.concurrency)
  results=await asyncio.gather(*(run_case(r,args,actor,sem) for r in rows))
 finally:
  if actor: await actor.shutdown()
 summary={'mode':args.mode,'total':len(results),'resolved':sum(bool(r.get('resolved')) for r in results),'eval_completed':sum(bool(r.get('eval_completed')) for r in results),'finished':sum(bool(r.get('finished')) for r in results),'errors':sum('error' in r for r in results),'agent_errors':sum('agent_error' in r for r in results),'source_only_resolved':sum(bool(r.get('source_only_resolved')) for r in results),'wall_seconds':time.time()-start,'results':results}
 dump(out/'summary.json',summary)
 print(json.dumps({k:v for k,v in summary.items() if k!='results'}),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--data',required=True); p.add_argument('--output',required=True)
 p.add_argument('--mode',choices=['baseline','oracle','react','claude','mini'],required=True); p.add_argument('--config'); p.add_argument('--gateway',action='store_true')
 p.add_argument('--base-url',default='http://172.30.90.3:8000/v1'); p.add_argument('--concurrency',type=int,default=4); p.add_argument('--limit',type=int)
 p.add_argument('--agent-timeout',type=int,default=1200); p.add_argument('--eval-timeout',type=int,default=600); p.add_argument('--max-tokens',type=int,default=2048); p.add_argument('--temperature',type=float,default=0.2)
 p.add_argument('--network',default='bridge'); p.add_argument('--context-length',type=int,default=65536)
 asyncio.run(main(p.parse_args()))
