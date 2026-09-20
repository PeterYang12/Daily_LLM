"""Check evidence and clean only this comparison's E2B instances."""
import argparse,asyncio,json,math,os,sys,time
from pathlib import Path
import httpx
sys.path.insert(0,'/lab/scripts')
from run_e2b_e2e import load_environment
from e2b import AsyncSandbox

async def main(root):
    assert (root/'COMPLETE').exists() and (root/'LOGIN_PROBE_COMPLETE').exists()
    p=json.loads((root/'protocol.json').read_text());labels={root.name,*[Path(x).name for x in p['variants'].values()]}
    audit={'time_unix':time.time(),'deployments':{},'trajectory_count':0,'controls':6}
    for label,path in p['variants'].items():
        provider='docker' if label=='docker' else 'e2b';base=Path(path);cases=list(base.glob(f'trial-*/{provider}/{p["task"]}/result.json'));assert len(cases)==3
        for case in cases:
            d=json.loads(case.read_text());assert d['eval_completed'] and d['trajectory_valid'] and d['cleanup_completed'] and not d.get('error')
            files=list(case.parent.glob('sessions/*/trajectories.jsonl'));assert len(files)==1
            for line in files[0].read_text().splitlines():
                t=json.loads(line)['trajectory'];assert len(t['response_ids'])==len(t['response_mask'])==len(t['response_logprobs'])
                assert all(x in (0,1) for x in t['response_mask']) and all(math.isfinite(x) for x in t['response_logprobs'])
                assert t['reward_score']==d['reward'];audit['trajectory_count']+=1
        if label=='docker':continue
        load_environment('/lab/secrets/e2b.env' if label=='remote_e2b' else '/lab/secrets/local-e2b/container.env')
        async with httpx.AsyncClient(headers={'X-API-Key':os.environ['E2B_API_KEY']},timeout=30) as client:
            async def inventory():
                r=await client.get(os.environ['E2B_API_URL'].rstrip('/')+'/sandboxes');r.raise_for_status();v=r.json()
                return v if isinstance(v,list) else v.get('sandboxes',v.get('items',[]))
            def owned(rows):
                result=[]
                for row in rows:
                    m=row.get('metadata') or {}
                    if isinstance(m,str):m=json.loads(m)
                    if m.get('experiment') in labels:result.append(row.get('sandboxID') or row.get('sandbox_id'))
                return result
            before=await inventory();mine=owned(before)
            for sid in mine:await AsyncSandbox.kill(sid)
            after=await inventory();assert not owned(after)
            audit['deployments'][label]={'owned_running_before':mine,'owned_running_after':[],'other_running_count':len(after)}
    controls=json.loads((root/'controls-audit.json').read_text());assert controls['matched'] and len(controls['controls'])==6
    audit['success']=True
    (root/'cleanup-audit.json').write_text(json.dumps(audit,indent=2)+'\n');print(json.dumps(audit))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);asyncio.run(main(p.parse_args().root))
