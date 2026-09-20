"""Summarize matched task runs without conflating sampled work and API latency."""
import argparse,csv,json,statistics,sys
from pathlib import Path
sys.path.insert(0,'/home/yuhanya/uni-agent-lab/scripts/swe_sandbox_profile')
from analyze import case_data

def read(path):return json.loads(path.read_text())
def local(path):return Path(path.replace('/lab','/home/yuhanya/uni-agent-lab',1))
def write_csv(path,rows):
    if not rows:return
    with path.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)

def main(root):
    protocol=read(root/'protocol.json');out=root/'analysis';out.mkdir(exist_ok=True)
    telemetry=[json.loads(line) for line in (root/'telemetry.jsonl').read_text().splitlines()]
    cases=[];tool_calls=[];micro=[];native=[]
    for variant,path in protocol['variants'].items():
        base=local(path);provider='docker' if variant=='docker' else 'e2b'
        for file in sorted(base.glob(f'trial-*/{provider}/{protocol["task"]}/result.json')):
            row,calls,events=case_data(file,telemetry);row['variant']=variant;row['source']=str(file)
            cases.append(row);tool_calls.extend(dict(x,variant=variant) for x in calls)
        m=base/'microbench'/provider/'summary.json'
        if m.exists():micro.extend(dict(variant=variant,**{k:v for k,v in x.items() if k!='samples'}) for x in read(m))
        n=base/'native-shell-probe.json'
        if n.exists():native.append(dict(variant=variant,**{k:v for k,v in read(n).items() if k!='samples'}))
    groups={}
    fields=['wall_seconds','agent_s','model_api_s','gateway_client_s','tools_s','tool_setup_s','provision_s','verifier_s','cleanup_s','generated_tokens','model_calls','tool_calls','gpu_busy_mean']
    for variant in protocol['variants']:
        rows=[x for x in cases if x['variant']==variant]
        if not rows:continue
        groups[variant]={'n':len(rows),'resolved':sum(x['resolved'] for x in rows),'valid_trajectories':sum(x['trajectory_valid'] for x in rows),
             'means':{k:statistics.mean(x[k] for x in rows if x[k] is not None) for k in fields},
             'wall_min_s':min(x['wall_seconds'] for x in rows),'wall_max_s':max(x['wall_seconds'] for x in rows),
             'wall_median_s':statistics.median(x['wall_seconds'] for x in rows),
             'model_seconds_per_1k_generated_tokens':sum(x['model_api_s'] for x in rows)/sum(x['generated_tokens'] for x in rows)*1000,
             'tool_seconds_per_call':sum(x['tools_s'] for x in rows)/sum(x['tool_calls'] for x in rows),
             'server_boundaries_match':all(x['server_metric_boundary_matches'] for x in rows)}
    summary={'complete':(root/'COMPLETE').exists() and len(cases)==9,'task':protocol['task'],'groups':groups,
             'microbench':micro,'native_shell':native,'scope':'One issue, three independent samples per deployment; fixed weights, not an RL training or general SWE score.'}
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');(out/'cases.json').write_text(json.dumps(cases,indent=2)+'\n')
    write_csv(out/'cases.csv',cases);write_csv(out/'tool-calls.csv',tool_calls);write_csv(out/'microbench.csv',micro)
    print(json.dumps({'complete':summary['complete'],'cases':len(cases),'groups':groups}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);main(p.parse_args().root)
