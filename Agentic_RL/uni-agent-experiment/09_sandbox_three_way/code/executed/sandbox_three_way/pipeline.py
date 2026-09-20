"""Serial balanced task comparison with independent controls and tool probes."""
import argparse,json,subprocess,time,urllib.request
from pathlib import Path

def main(root):
    protocol=json.loads((root/'protocol.json').read_text());events=[]
    def stage(label,mode,tag='controls'):
        path=Path(protocol['variants'][label]);provider='docker' if label=='docker' else 'e2b'
        expected=path/'microbench'/provider/'summary.json' if mode=='micro' else path/'native-shell-probe.json' if mode=='native' else path/(tag+'-summary.json')
        if expected.exists():return
        cmd=['/lab/envs/cpu/bin/python','/lab/scripts/sandbox_three_way/worker.py','--root',str(path),'--variant',label,'--mode',mode,'--tag',tag,'--base-url',protocol['model_base_url']]
        record={'variant':label,'mode':mode,'tag':tag,'started_unix':time.time(),'command':cmd}
        print('START',label,mode,tag,flush=True)
        with (root/f'{label}-{mode}-{tag}.log').open('w') as log:
            result=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
        record.update(exit_code=result.returncode,ended_unix=time.time());events.append(record)
        (root/'stages.json').write_text(json.dumps(events,indent=2)+'\n')
        assert result.returncode==0,record
        if mode=='react':
            results=json.loads(expected.read_text())['results']
            assert len(results)==1 and all(r.get('eval_completed') and r.get('trajectory_valid') and not r.get('error') for r in results),results
        print('DONE',label,mode,tag,flush=True)
    for label in protocol['variants']:stage(label,'micro')
    for label in ['local_e2b','remote_e2b']:stage(label,'native')
    for label in protocol['variants']:stage(label,'controls')
    heads=[];controls=[]
    for label,path in protocol['variants'].items():
        provider='docker' if label=='docker' else 'e2b';base=Path(path)/'controls'/provider/protocol['task']
        for mode in ['baseline','oracle']:
            r=json.loads((base/mode/'result.json').read_text())
            assert r.get('eval_completed') and r.get('resolved')==(mode=='oracle') and not r.get('error'),r
            controls.append({'variant':label,'mode':mode,'resolved':r['resolved'],'eval_completed':r['eval_completed']})
        heads.append(json.loads((base/'baseline/agent-environment/initial-state.json').read_text()))
    assert len({h['head'] for h in heads})==len({h['tree'] for h in heads})==1,heads
    (root/'controls-audit.json').write_text(json.dumps({'controls':controls,'initial_states':heads,'matched':True},indent=2)+'\n')
    deadline=time.monotonic()+600
    while True:
        try:
            with urllib.request.urlopen(protocol['model_base_url']+'/models',timeout=3) as r:assert r.status==200
            break
        except Exception:
            if time.monotonic()>deadline:raise
            time.sleep(3)
    for index,order in enumerate(protocol['order']):
        for label in order:stage(label,'react',f'trial-{index+1}')
    (root/'COMPLETE').write_text(str(time.time())+'\n')
    print('ALL STUDY STAGES COMPLETE',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);main(p.parse_args().root)
