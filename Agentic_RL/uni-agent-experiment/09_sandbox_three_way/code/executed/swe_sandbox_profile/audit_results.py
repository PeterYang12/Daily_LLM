"""Recheck completed experiment evidence independently of the report tables."""
import argparse,hashlib,json,math,time
from pathlib import Path

def read(path):return json.loads(path.read_text())

def main(root):
    for marker in ['FOLLOWUP_COMPLETE','NATIVE_PROBE_COMPLETE','VERIFIER_PROBE_COMPLETE']:
        assert (root/marker).exists(),marker
    primary=sorted((root/'main-optimized').glob('*/*/result.json'))
    assert len(primary)==24
    ids={r['instance_id'] for r in read(root/'manifest.json')['tasks']}
    totals={}
    for provider in ['docker','e2b']:
        results=[read(p) for p in primary if p.parent.parent.name==provider]
        assert len(results)==12 and {r['instance_id'] for r in results}==ids
        assert all(r['eval_completed'] and r['trajectory_valid'] and r['cleanup_completed'] and 'error' not in r for r in results)
        totals[provider]={'n':len(results),'resolved':sum(r['resolved'] for r in results),'finished':sum(r['finished'] for r in results)}
    scheduling=sorted(root.glob('scheduling-*-summary.json'));assert len(scheduling)==4
    scheduling_paths=[]
    for path in scheduling:
        d=read(path);assert len(d['results'])==4
        assert all(r['resolved'] and r['eval_completed'] and r['trajectory_valid'] and r['cleanup_completed'] for r in d['results'])
        scheduling_paths.extend(sorted((root/d['tag']).glob('*/*/result.json')))
    trajectories=0;generated=0
    for path in primary+scheduling_paths:
        result=read(path)
        records=[]
        for source in path.parent.glob('sessions/*/trajectories.jsonl'):
            records.extend(json.loads(line)['trajectory'] for line in source.read_text().splitlines())
        assert records,path
        for t in records:
            response,mask,lp=t['response_ids'],t['response_mask'],t['response_logprobs']
            assert response and len(response)==len(mask)==len(lp),path
            assert all(m in (0,1) for m in mask) and all(math.isfinite(x) for x in lp),path
            assert t['reward_score']==result['reward'] and t['finished']==result['finished'],path
            trajectories+=1;generated+=sum(mask)
    sources=read(root/'controls-final-index.json')['sources'];controls=0
    for iid in ids:
        states=[]
        for provider in ['docker','e2b']:
            for mode in ['baseline','oracle']:
                base=root/sources[iid]/provider/iid/mode
                row=read(base/'result.json')
                assert row['eval_completed'] and row['resolved']==(mode=='oracle') and 'error' not in row,base
                controls+=1
            states.append(read(root/sources[iid]/provider/iid/'baseline/agent-environment/initial-state.json'))
        assert states[0]['head']==states[1]['head'] and states[0]['tree']==states[1]['tree'],iid
    serving=read(root/'inference-benchmark/raw.json');overhead=read(root/'inference-benchmark/logprob-overhead.json')['results']
    assert len(serving)==54 and len(overhead)==24
    assert len({(r['backend'],r['input_length'],r['concurrency'],r['repeat']) for r in serving})==54
    assert len({(r['backend'],r['concurrency'],r['repeat'],r['logprobs']) for r in overhead})==24
    assert all(r['output_tokens']==256*r['concurrency'] and len(r['requests'])==r['concurrency'] for r in serving)
    for row in serving:
        assert all(q['usage']['prompt_tokens']==row['input_length'] and q['usage']['completion_tokens']==256 and q['ttft_s'] is not None for q in row['requests'])
    probe=read(root/'verifier-cpu-probe.json')
    assert len(probe)==12 and all(r['resolved'] and r['resources'] for r in probe)
    assert all(all(k in m for k in ['body_wall_s','user_cpu_s','system_cpu_s']) for r in probe for m in r['resources'])
    native=read(root/'native-shell-probe.json')
    micro=sum(row['n'] for p in ['docker','e2b'] for row in read(root/'microbench'/p/'summary.json'))
    assert micro==200
    provenance=read(root/'runtime-provenance.json')
    for source,digest in provenance['source_sha256'].items():
        frozen=root/'code-snapshot'/Path(source).name
        assert hashlib.sha256(frozen.read_bytes()).hexdigest()==digest,source
    audit={'time_unix':time.time(),'status':'passed','primary':totals,'accepted_controls':controls,
           'scheduling_runs':len(scheduling_paths),'rechecked_trajectories':trajectories,'generated_mask_tokens':generated,
           'serving_batches':len(serving),'logprob_batches':len(overhead),'fixed_operation_samples':micro,
           'gold_verifier_resource_runs':len(probe),'native_shell_success':native.get('success',False),
           'frozen_primary_source_files':len(provenance['source_sha256']),
           'scope':'Offline evidence audit; no new model or sandbox operations.'}
    (root/'completion-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    print(json.dumps(audit))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);main(p.parse_args().root)
