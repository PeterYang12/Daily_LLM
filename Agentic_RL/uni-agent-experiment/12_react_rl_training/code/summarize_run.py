"""Summarize observed RL updates and consumed batches without changing the run."""
import argparse,collections,hashlib,json,math,statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load(path):return json.loads(Path(path).read_text())
def jsonlines(path):
    if not Path(path).exists():return []
    rows=[]
    for line in Path(path).read_text().splitlines():
        try:rows.append(json.loads(line))
        except json.JSONDecodeError:continue
    return rows
def stats(values):
    values=sorted(float(x) for x in values if x is not None and math.isfinite(float(x)))
    if not values:return {'n':0}
    return {'n':len(values),'mean':statistics.mean(values),'median':statistics.median(values),
            'p95':values[min(len(values)-1,math.ceil(.95*len(values))-1)],'min':values[0],'max':values[-1]}
def wilson(success,n):
    if not n:return [None,None]
    z=1.95996398454;p=success/n;den=1+z*z/n
    center=(p+z*z/(2*n))/den
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [center-half,center+half]

def main(args):
    raw=jsonlines(ROOT/'results/main-metrics.jsonl')
    # Multiple attempts remain in timestamped logs on resume. The final metric
    # for each logical step is the one used for the progress curve.
    by_step={row['step']:row['data'] for row in raw};steps=sorted(s for s in by_step if s>0)
    frozen=load(ROOT/'data/frozen-manifest.json')
    result={'observed_steps':steps,'last_step':max(steps,default=0),'target_steps':300,'raw_metric_rows':len(raw),
            'train_tasks':frozen['train']['n'],'validation_tasks':frozen['val']['n'],
            'training_updates':[{'step':s,**by_step[s]} for s in steps],
            'timings':{},'validation':[],'consumed_batches':[]}
    for name in ['step','gen','old_log_prob','adv','update_actor','update_weights','save_checkpoint','testing']:
        result['timings'][name]=stats(by_step[s].get('timing_s/'+name) for s in steps)
    result['nonzero_gradient_updates']=sum(by_step[s].get('actor/grad_norm',0)>0 for s in steps)
    result['sampled_parameter_change_updates']=sum(by_step[s].get('weights/sample_changed_fraction',0)>0 for s in steps)
    acc_key='val-core/princeton-nlp/SWE-bench_Verified/acc/mean@1'
    for s,data in sorted(by_step.items()):
        if acc_key in data:
            n=frozen['val']['n'];resolved=round(data[acc_key]*n)
            result['validation'].append({'step':s,'resolved':resolved,'n':n,'accuracy':data[acc_key],'wilson_95':wilson(resolved,n)})
    for step in steps:
        rows=jsonlines(ROOT/'results/rollouts'/f'{step}.jsonl')
        groups=collections.defaultdict(list)
        for row in rows:groups[str(row['uid']).rsplit('_',2)[0]].append(float(row['score']))
        result['consumed_batches'].append({'step':step,'trajectories':len(rows),'prompt_groups':len(groups),
            'group_sizes':sorted(len(v) for v in groups.values()),'reward_sum':sum(float(r['score']) for r in rows),
            'all_fail_groups':sum(all(v==0 for v in values) for values in groups.values()),
            'all_pass_groups':sum(all(v==1 for v in values) for values in groups.values()),
            'mixed_reward_groups':sum(min(values)!=max(values) for values in groups.values())})
    result['consumed_trajectories']=sum(r['trajectories'] for r in result['consumed_batches'])
    result['mixed_reward_groups']=sum(r['mixed_reward_groups'] for r in result['consumed_batches'])
    result['total_prompt_groups']=sum(r['prompt_groups'] for r in result['consumed_batches'])
    result['step_sequence_complete']=steps==list(range(1,301))
    result['batch_shape_valid']=all(r['trajectories']==16 and r['prompt_groups']==4 and r['group_sizes']==[4]*4 for r in result['consumed_batches'])
    result['frozen_data_hashes_valid']=all(hashlib.sha256((ROOT/'data'/(name+'.parquet')).read_bytes()).hexdigest()==row['sha256'] for name,row in frozen.items())
    result['exit']=load(ROOT/'results/main-exit.json') if (ROOT/'results/main-exit.json').exists() else None
    result['checkpoint_dirs']=[p.name for p in (ROOT/'checkpoints').glob('global_step_*') if p.is_dir()]
    if args.final:
        assert result['step_sequence_complete'] and result['batch_shape_valid'] and result['frozen_data_hashes_valid']
        assert result['exit'] and result['exit']['exit_code']==0
        assert result['checkpoint_dirs']==['global_step_300']
        assert (ROOT/'checkpoints/global_step_300/actor/ckpt_contents.json').exists()
        assert {r['step'] for r in result['validation']}=={0,100,200,300}
    path=ROOT/'results'/('training-summary.json' if args.final else 'training-progress-summary.json')
    path.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['last_step','consumed_trajectories','nonzero_gradient_updates','batch_shape_valid','validation']}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--final',action='store_true');main(p.parse_args())
