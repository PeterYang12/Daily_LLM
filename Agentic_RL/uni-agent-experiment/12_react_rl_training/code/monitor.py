"""Host-side time-aligned GPU and owned-sandbox inventory sampling."""
import argparse,json,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
secret=dict(x.split('=',1) for x in (ROOT.parent/'secrets/user-e2b-10-145-64-67.env').read_text().splitlines())
p=argparse.ArgumentParser();p.add_argument('--phase',default='main');p.add_argument('--interval',type=float,default=2);a=p.parse_args()
out=ROOT/'results'/a.phase;out.mkdir(parents=True,exist_ok=True)
stop=ROOT/'state'/('stop-monitor-'+a.phase)
with (out/'telemetry.jsonl').open('a',buffering=1) as f:
    while not stop.exists():
        start=time.monotonic();r={'time_unix':time.time()}
        try:
            q=subprocess.run(['rocm-smi','--showuse','--showmeminfo','vram','--json'],text=True,capture_output=True,timeout=15)
            r['gpu']=json.loads(q.stdout)
        except Exception as exc:r['gpu_error']=type(exc).__name__
        try:
            req=urllib.request.Request(secret['E2B_API_URL']+'/sandboxes',headers={'X-API-Key':secret['E2B_API_KEY']})
            with urllib.request.urlopen(req,timeout=10) as response:boxes=json.load(response)
            owned=[b for b in boxes if (b.get('metadata') or {}).get('experiment')=='ua-rl-300']
            r['owned_sandbox_count']=len(owned);r['other_sandbox_count']=len(boxes)-len(owned)
            r['qualification_sandbox_count']=sum(str((b.get('metadata') or {}).get('session','')).startswith('qualification-') for b in owned)
            r['rollout_sandbox_count']=len(owned)-r['qualification_sandbox_count']
            r['owned']=[{'id':b.get('sandboxID'),'session':(b.get('metadata') or {}).get('session'),'cpu_count':b.get('cpuCount'),'memory_mb':b.get('memoryMB')} for b in owned]
        except Exception as exc:r['sandbox_error']=type(exc).__name__
        f.write(json.dumps(r)+'\n')
        time.sleep(max(.1,a.interval-(time.monotonic()-start)))
