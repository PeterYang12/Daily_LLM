"""A bounded, read-only sample of GPU engine/memory activity and container CPU."""
import argparse,json,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--seconds',type=int,default=300);p.add_argument('--interval',type=float,default=2);a=p.parse_args()
pid=subprocess.check_output(['docker','inspect','ua-rl-gpu','--format','{{.State.Pid}}'],text=True).strip()
cg=Path('/proc')/pid/'cgroup'
line=next(x for x in cg.read_text().splitlines() if x.startswith('0::'))
directory=Path('/sys/fs/cgroup')/line.split('::',1)[1].lstrip('/')
start=time.monotonic();previous=None
with (ROOT/'results/hardware-phase-probe.jsonl').open('a',buffering=1) as stream:
    while time.monotonic()-start<a.seconds:
        t=time.monotonic();row={'time_unix':time.time()}
        try:
            data=json.loads(subprocess.check_output(['amd-smi','metric','--usage','--pcie','--json'],text=True,timeout=10))
            row['gpus']=[{'gpu':r['gpu'],'gfx_pct':r['usage']['gfx_activity'],'umc_pct':r['usage']['umc_activity'],'pcie':r['pcie']} for r in data['gpu_data']]
        except Exception as exc:row['gpu_error']=type(exc).__name__
        try:
            cpu={k:int(v) for k,v in (x.split() for x in (directory/'cpu.stat').read_text().splitlines())}
            row['cpu_stat']=cpu;row['cpu_max']=(directory/'cpu.max').read_text().strip();row['memory_bytes']=int((directory/'memory.current').read_text())
            if previous:
                elapsed=row['time_unix']-previous['time_unix']
                row['cpu_cores_used']=(cpu['usage_usec']-previous['cpu_stat']['usage_usec'])/1e6/elapsed
                row['throttled_usec_delta']=cpu['throttled_usec']-previous['cpu_stat']['throttled_usec']
            previous=row
        except Exception as exc:row['cpu_error']=type(exc).__name__
        stream.write(json.dumps(row)+'\n')
        time.sleep(max(.1,a.interval-(time.monotonic()-t)))
