"""Bounded API capacity probe on real SWE sandboxes; always remove owned VMs."""
import argparse,asyncio,time
from e2b import AsyncSandbox
from ua_rl.common import ROOT,load_e2b,save

async def main(args):
    load_e2b();boxes=[];result={'target':args.n,'cpu_per_sandbox':4,'memory_mib_per_sandbox':8192,'started_unix':time.time(),'creates':[]}
    try:
        for i in range(args.n):
            t=time.monotonic()
            try:
                sb=await AsyncSandbox.create('vatm00lmw03h9y86956r',timeout=180,request_timeout=30,metadata={'experiment':'ua-rl-capacity-probe'})
                boxes.append(sb);result['creates'].append({'id':sb.sandbox_id,'create_s':time.monotonic()-t})
            except Exception as exc:result['error']=f'{type(exc).__name__}: {exc}';break
        result['running']=sum(await asyncio.gather(*(sb.is_running() for sb in boxes)))
    finally:
        result['cleanup']=await asyncio.gather(*(sb.kill() for sb in boxes),return_exceptions=True)
        result['all_stopped']=not any(await asyncio.gather(*(sb.is_running() for sb in boxes)))
        save(ROOT/'results'/f'sandbox-capacity-{args.n}.json',result)
        print({'target':args.n,'running':result.get('running'),'all_stopped':result['all_stopped'],'error':result.get('error')})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--n',type=int,default=8);a=p.parse_args();assert 1<=a.n<=16
    asyncio.run(main(a))
