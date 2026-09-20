"""Remove only terminated-run sandboxes, protecting ongoing data qualification."""
import argparse,asyncio,json,os
from pathlib import Path
import httpx
from ua_rl.common import ROOT,load_e2b,save

async def main(args):
    load_e2b();owned=set()
    for p in (ROOT/'results/sessions').glob('*/identity.json'):
        d=json.loads(p.read_text())
        if d.get('run_phase')==args.phase:owned.add(d['session_id'])
    async with httpx.AsyncClient(base_url=os.environ['E2B_API_URL'],headers={'X-API-Key':os.environ['E2B_API_KEY']},timeout=30) as c:
        response=await c.get('/sandboxes');response.raise_for_status();boxes=response.json()
        targets=[b for b in boxes if (b.get('metadata') or {}).get('experiment')=='ua-rl-300' and (b.get('metadata') or {}).get('session') in owned]
        removed=[]
        for b in targets:
            sid=b['sandboxID'];response=await c.delete('/sandboxes/'+sid)
            if response.status_code not in [204,404]:response.raise_for_status()
            removed.append(sid)
        response=await c.get('/sandboxes');response.raise_for_status()
        remaining=[b['sandboxID'] for b in response.json() if (b.get('metadata') or {}).get('session') in owned]
    report={'phase':args.phase,'removed':removed,'remaining':remaining,'success':not remaining}
    save(ROOT/'results'/(args.phase+'-final-cleanup.json'),report);print(report);assert report['success']

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',required=True);asyncio.run(main(p.parse_args()))
