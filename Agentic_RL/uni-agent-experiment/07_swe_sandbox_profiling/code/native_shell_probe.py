"""Bounded E2B persistent-stdin transport prototype; no agent benchmark claims."""
import argparse,asyncio,json,os,statistics,sys,time,uuid
from pathlib import Path
sys.path.insert(0,'/lab/scripts')
from e2b import AsyncSandbox
from run_e2b_e2e import load_environment

async def main(args):
    if args.wait:
        while not (args.root/'FOLLOWUP_COMPLETE').exists():await asyncio.sleep(5)
    load_environment('/lab/secrets/e2b.env')
    template=next(t['build']['template_id'] for t in json.loads((args.root/'templates.json').read_text()) if t['instance_id']=='pallets__flask-5014')
    result={'scope':'Persistent stdin transport prototype only; not the provider used for primary SWE task scores.'}
    sb=None;handle=None
    try:
        sb=await AsyncSandbox.create(template=template,timeout=600,metadata={'experiment':args.root.name,'purpose':'native-shell-prototype'},request_timeout=60)
        result['sandbox_id']=sb.sandbox_id
        event=asyncio.Event()
        handle=await sb.commands.run('/bin/bash --noprofile --norc',background=True,stdin=True,timeout=0,
            user='root',on_stdout=lambda _:event.set(),on_stderr=lambda _:event.set(),request_timeout=30)
        async def command(text):
            marker='__UA_DONE_'+uuid.uuid4().hex
            start_out=len(handle.stdout);start_err=len(handle.stderr)
            payload=text+'\n__ua_rc=$?\n'+f"printf '\\n{marker} %s\\n' \"$__ua_rc\"\nprintf '\\n{marker}\\n' >&2\n"
            started=time.perf_counter();await handle.send_stdin(payload,request_timeout=30)
            deadline=time.monotonic()+15
            while True:
                stdout=handle.stdout[start_out:];stderr=handle.stderr[start_err:]
                if marker in stdout and marker in stderr:break
                if handle.exit_code is not None:raise RuntimeError('Persistent shell exited before completion marker')
                if time.monotonic()>deadline:raise TimeoutError('Persistent command did not complete')
                event.clear()
                try:await asyncio.wait_for(event.wait(),timeout=.2)
                except TimeoutError:pass
            status=int(stdout.split(marker,1)[1].splitlines()[0].strip())
            return {'elapsed_s':time.perf_counter()-started,'exit_code':status,
                    'stdout':stdout.split('\n'+marker,1)[0],'stderr':stderr.split('\n'+marker,1)[0]}
        await command('true')
        await command('cd /tmp; export UA_NATIVE_CHECK=ok')
        check=await command('printf "%s" "$PWD:$UA_NATIVE_CHECK"')
        result['cwd_env_persist']=check['stdout']=='/tmp:ok'
        samples=[await command('true') for _ in range(30)]
        if not all(x['exit_code']==0 for x in samples):raise RuntimeError('A no-op command failed')
        durations=sorted(x['elapsed_s'] for x in samples)
        result.update(samples=samples,n=len(samples),median_s=statistics.median(durations),
                      mean_s=statistics.mean(durations),p95_s=durations[int(.95*(len(durations)-1))])
        await handle.close_stdin(request_timeout=10)
        await asyncio.wait_for(handle.wait(),timeout=10)
        result['success']=result['cwd_env_persist']
    except Exception as exc:
        result['error']=str(exc).replace(os.environ['E2B_API_KEY'],'[REDACTED]')
        result['success']=False
    finally:
        if sb:
            try:result['kill_ack']=await sb.kill(request_timeout=30)
            except Exception as exc:result['cleanup_error']=str(exc)
        (args.root/'native-shell-probe.json').write_text(json.dumps(result,indent=2)+'\n')
        (args.root/'NATIVE_PROBE_COMPLETE').write_text(str(time.time())+'\n')
        print(json.dumps({k:v for k,v in result.items() if k!='samples'}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);p.add_argument('--wait',action='store_true');asyncio.run(main(p.parse_args()))
