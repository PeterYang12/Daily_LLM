"""Check exported token arrays and connect them to task outcomes and profiles."""
import argparse,json,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]

def main(args):
    frozen=json.loads((ROOT/'data/frozen-manifest.json').read_text())
    train_ids=set(frozen['train']['instance_ids']);val_ids=set(frozen['val']['instance_ids'])
    locations={p.parent.name:p for p in (ROOT/'results/trajectories').rglob('trajectory.json')}
    rows=[];errors=[];unexported=[]
    for ident in (ROOT/'results/sessions').glob('*/identity.json'):
        identity=json.loads(ident.read_text())
        if identity.get('run_phase')!='main':continue
        sid=identity['session_id'];iid=identity['instance_id'];folder=ident.parent
        assert iid in train_ids|val_ids
        path=locations.get(sid)
        if path is None or not path.with_suffix('.npz').exists():
            unexported.append({'session_id':sid,'instance_id':iid,'task_result_exists':(folder/'result.json').exists(),'runner_error_exists':(folder/'error.json').exists()});continue
        meta=json.loads(path.read_text())
        try:
            with np.load(path.with_suffix('.npz'),allow_pickle=False) as arrays:
                for i,t in enumerate(meta['trajectories']):
                    prefix=f'traj{i}_';pid=arrays[prefix+'prompt_ids'];ids=arrays[prefix+'response_ids'];mask=arrays[prefix+'response_mask'];lp=arrays[prefix+'response_logprobs']
                    valid=len(ids)==len(mask)==len(lp) and len(pid)==t['prompt_len'] and len(ids)==t['response_len'] and np.isin(mask,[0,1]).all() and np.isfinite(lp).all()
                    row={'session_id':sid,'instance_id':iid,'partition':'train' if iid in train_ids else 'validation','trajectory_index':i,'valid':bool(valid),
                         'prompt_tokens':len(pid),'response_tokens':len(ids),'trainable_model_tokens':int(mask.sum()),'num_turns':t['num_turns'],'finished':t['finished'],'reward':t['reward_score'],
                         'profile_metrics':t.get('reward_metrics',{})}
                    result_path=folder/'result.json'
                    if result_path.exists():
                        task_result=json.loads(result_path.read_text());row['task_reward']=task_result['reward'];row['reward_matches_task']=task_result['reward']==t['reward_score']
                    spans_path=folder/'spans.json'
                    if spans_path.exists():
                        spans=json.loads(spans_path.read_text())['events']
                        row['raw_generated_tokens']=sum((e.get('usage') or {}).get('completion_tokens',0) or 0 for e in spans if e['category']=='model_roundtrip')
                        row['exec_modes']=sorted({e.get('exec_mode','sdk_login') for e in spans if e['category']=='sandbox_exec'})
                        row['verifier_body_s']=sum(e.get('body_s',0) for e in spans if e['category']=='verifier_exec')
                    rows.append(row)
                    if not valid:errors.append(sid)
        except (OSError,ValueError,KeyError) as exc:
            errors.append({'session_id':sid,'error':type(exc).__name__+': '+str(exc)})
    report={'exported_trajectories':len(rows),'rows':rows,'errors':errors,'unexported_sessions':unexported,
            'note':'Unexported sessions can be cancelled/unconsumed async work. Consumed batch counts are audited separately from trainer dumps.'}
    (ROOT/'results/trajectory-audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'exported':len(rows),'errors':len(errors),'unexported':len(unexported)}))
    if args.final:
        assert not errors
        assert all(r.get('reward_matches_task',True) for r in rows)
        assert all(r.get('exec_modes')==['native'] for r in rows)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--final',action='store_true');main(p.parse_args())
