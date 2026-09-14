"""Canonical candidate-only replay, removing pre-existing changes from SWE images.
No agent is re-run. All candidates, irrespective of prior score, follow this rule.
"""
import argparse,asyncio,copy,hashlib,json,time
from pathlib import Path
from datasets import load_dataset
from run_swe import sandbox,evaluate,source_patch
from lab_runtime import checked,dump

async def one(row,args,sem):
 async with sem:
  task=row['extra_info']['tools_kwargs']['task']; m=task['metadata']; iid=m['instance_id']; old=Path(args.input)/iid; out=Path(args.output)/iid
  if (out/'result.json').exists(): return json.loads((out/'result.json').read_text())
  if not (old/'result.json').exists(): return None
  original=json.loads((old/'result.json').read_text())
  result={k:original.get(k) for k in ['instance_id','repo','mode','image','finished','agent_seconds','agent_info','agent_error','trajectory_count','trajectory_error']}
  result.update(resolved=False,eval_completed=False,source_only_resolved=False,raw_result_path=str(old/'result.json'))
  out.mkdir(parents=True,exist_ok=True); start=time.time()
  try:
   full=(old/'candidate.patch').read_text()
   result['captured_patch_sha256']=hashlib.sha256(full.encode()).hexdigest()
   async with sandbox(task['sandbox']['image'],out/'attribution') as s:
    await checked(s,['git','config','--global','--add','safe.directory','/testbed'])
    await checked(s,['git','config','core.fileMode','false'],workdir='/testbed')
    baseline=(await checked(s,['git','diff','--binary',m['base_commit']],workdir='/testbed')).stdout
    (out/'image-baseline.patch').write_text(baseline)
    tracked=set((await checked(s,['git','ls-files'],workdir='/testbed')).stdout.splitlines())
    await checked(s,['git','add','-u'],workdir='/testbed')
    tree=(await checked(s,['git','write-tree'],workdir='/testbed')).stdout.strip()
    # Dedicated patch-transformation sandbox only: build captured candidate on source base.
    await checked(s,['git','reset','--hard',m['base_commit']],workdir='/testbed')
    if full:
     await s.write_file('/tmp/captured.patch',full)
     await checked(s,['git','apply','--whitespace=nowarn','/tmp/captured.patch'],workdir='/testbed')
    await checked(s,['git','add','-N','--','.'],workdir='/testbed')
    delta=(await checked(s,['git','diff','--binary',tree],workdir='/testbed')).stdout
    (out/'candidate-delta.patch').write_text(delta)
    result.update(image_baseline_patch_bytes=len(baseline.encode()),image_initial_tree=tree,candidate_delta_bytes=len(delta.encode()))
   source,test_paths=source_patch(delta); (out/'candidate-source-only.patch').write_text(source)
   result['test_paths_changed']=test_paths
   result['existing_tests_modified']=[p for p in test_paths if p in tracked]
   result['added_test_paths']=[p for p in test_paths if p not in tracked]
   result.update(await evaluate(m,task['sandbox']['image'],delta,out/'verifier',args.eval_timeout,'bridge'))
   if test_paths:
    r=await evaluate(m,task['sandbox']['image'],source,out/'source-only-verifier',args.eval_timeout,'bridge')
    result['source_only_resolved']=r['resolved']; result['source_only_eval_completed']=r['eval_completed']
   else:
    result['source_only_resolved']=result['resolved']; result['source_only_eval_completed']=result['eval_completed']
  except Exception as e:
   result['error']=repr(e)
  result['regrade_seconds']=time.time()-start
  dump(out/'result.json',result)
  print(json.dumps({k:result.get(k) for k in ['instance_id','resolved','eval_completed','finished','source_only_resolved','existing_tests_modified','error']}),flush=True)
  return result

async def main(a):
 out=Path(a.output); out.mkdir(parents=True,exist_ok=True); dump(out/'policy.json',{'input':a.input,'data':a.data,'method':'Replay captured full diff on source base, diff resulting tree against pre-existing image state, apply only agent delta in fresh grading container. Same policy for every completed candidate, no new generation.'})
 rows=load_dataset('parquet',data_files=a.data,split='train').to_list(); sem=asyncio.Semaphore(a.concurrency)
 results=[r for r in await asyncio.gather(*(one(r,a,sem) for r in rows)) if r is not None]
 summary={'total':len(results),'expected':len(rows),'resolved':sum(r['resolved'] for r in results),'eval_completed':sum(r['eval_completed'] for r in results),'finished':sum(bool(r.get('finished')) for r in results),'source_only_resolved':sum(r['source_only_resolved'] for r in results),'existing_test_modification_cases':sum(bool(r.get('existing_tests_modified')) for r in results),'errors':sum(bool(r.get('error')) for r in results),'results':results}
 dump(out/'summary.json',summary)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',required=True);p.add_argument('--data',required=True);p.add_argument('--concurrency',type=int,default=3);p.add_argument('--eval-timeout',type=int,default=600)
 asyncio.run(main(p.parse_args()))
