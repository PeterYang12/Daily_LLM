import json,math
from pathlib import Path
root=Path('/lab/results'); runs=[]
for run in sorted(root.glob('*')):
 if not (run/'sessions').is_dir(): continue
 rows=[]
 for p in sorted((run/'sessions').glob('*/trajectories.jsonl')):
  checks=[]; gen=0; observed=0; turns=0; count=0; issues=[]
  for line in p.read_text().splitlines():
   record=json.loads(line); t=record['trajectory']; count+=1
   ids=t['response_ids']; mask=t['response_mask']; lp=t.get('response_logprobs')
   if len(ids)!=len(mask): issues.append('token_mask_length_mismatch')
   if any(x not in (0,1) for x in mask): issues.append('nonbinary_mask')
   if lp is None: issues.append('missing_logprobs')
   elif len(lp)!=len(ids): issues.append('token_logprob_length_mismatch')
   elif not all(isinstance(x,(float,int)) and math.isfinite(x) for x in lp): issues.append('nonfinite_logprob')
   if not all(isinstance(x,int) and 0<=x<151936 for x in ids+t['prompt_ids']): issues.append('invalid_token_id')
   gen+=sum(mask); observed+=len(mask)-sum(mask); turns+=t['num_turns'] or 0
  rp=run/p.parent.name/'result.json'; outcome=json.loads(rp.read_text()) if rp.exists() else {}
  rows.append({'instance_id':p.parent.name,'trajectories':count,'generated_tokens_mask_1':gen,'observation_tokens_mask_0':observed,'trajectory_turns':turns,'issues':sorted(set(issues)),'raw_reward_fields':'not joined by debug gateway; see TaskResult','task_finished':outcome.get('finished'),'task_resolved':outcome.get('resolved'),'jsonl_bytes':p.stat().st_size})
 summary={'run':run.name,'sessions':len(rows),'valid_token_mask_sessions':sum(not any(e in r['issues'] for e in ['token_mask_length_mismatch','nonbinary_mask','invalid_token_id']) and r['trajectories']>0 for r in rows),'rl_logprobs_complete_sessions':sum(not r['issues'] and r['trajectories']>0 for r in rows),'generated_tokens':sum(r['generated_tokens_mask_1'] for r in rows),'observation_tokens':sum(r['observation_tokens_mask_0'] for r in rows),'rows':rows}
 runs.append(summary)
(root/'trajectory-audit.json').write_text(json.dumps(runs,indent=2))
for d in runs: print({k:v for k,v in d.items() if k!='rows'})
