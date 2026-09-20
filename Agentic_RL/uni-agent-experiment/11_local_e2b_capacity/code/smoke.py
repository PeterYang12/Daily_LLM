"""Exercise local E2B lifecycle, files, execution, native stdin, and concurrent VMs."""
import argparse,asyncio,json,os,sys,time,uuid
from pathlib import Path
sys.path.insert(0,'/lab/scripts')
from run_e2b_e2e import load_environment
from e2b import AsyncSandbox,CommandExitException

async def main(args):
    load_environment(args.env_file)
    assert '172.30.90.1:18090' in os.environ['E2B_API_URL'],'Refusing an unexpected server'
    report={'started_unix':time.time(),'endpoint':os.environ['E2B_API_URL'],'checks':{},'sandboxes':[]}
    sb=None
    try:
        started=time.perf_counter()
        sb=await AsyncSandbox.create('base',timeout=300,metadata={'experiment':'ua-local-e2b','purpose':'smoke'},request_timeout=60)
        report['sandboxes'].append(sb.sandbox_id);report['create_s']=time.perf_counter()-started
        report['checks']['running']=await sb.is_running()
        result=await sb.commands.run('printf "%s:%s" "$PWD" "$UA_TEST"',cwd='/tmp',envs={'UA_TEST':'held'},user='root')
        report['checks']['cwd_env']=result.stdout=='/tmp:held' and result.exit_code==0
        await sb.files.write('/tmp/ua-text.txt','hello sandbox\n',user='root')
        report['checks']['text_file']=await sb.files.read('/tmp/ua-text.txt',user='root')=='hello sandbox\n'
        blob=bytes(range(256))*4096
        await sb.files.write('/tmp/ua-binary.bin',blob,user='root')
        report['checks']['binary_1MiB']=bytes(await sb.files.read('/tmp/ua-binary.bin',format='bytes',user='root'))==blob
        try:
            await sb.commands.run("printf out; printf err >&2; exit 7",user='root')
            report['checks']['nonzero_exit']=False
        except CommandExitException as exc:
            report['checks']['nonzero_exit']=exc.exit_code==7 and exc.stdout=='out' and exc.stderr=='err'
        event=asyncio.Event()
        handle=await sb.commands.run('/bin/bash --noprofile --norc',background=True,stdin=True,timeout=0,user='root',on_stdout=lambda _:event.set())
        marker='UA_DONE_'+uuid.uuid4().hex
        await handle.send_stdin('cd /tmp\nexport UA_STATE=retained\n')
        await handle.send_stdin(f'printf "%s:%s" "$PWD" "$UA_STATE"\nprintf "\\n{marker}\\n"\n')
        deadline=time.monotonic()+15
        while marker not in handle.stdout:
            if time.monotonic()>deadline:raise TimeoutError('Persistent stdin did not return marker')
            event.clear()
            try:await asyncio.wait_for(event.wait(),.2)
            except asyncio.TimeoutError:pass
        report['checks']['persistent_shell']='/tmp:retained' in handle.stdout
        await handle.close_stdin();await asyncio.wait_for(handle.wait(),10)
    finally:
        if sb:report['checks']['kill_ack']=await sb.kill()
    if sb:report['checks']['destroyed']=not await sb.is_running()
    async def one(index):
        vm=None;t=time.perf_counter()
        try:
            vm=await AsyncSandbox.create('base',timeout=180,metadata={'experiment':'ua-local-e2b','purpose':'concurrent-smoke'},request_timeout=60)
            report['sandboxes'].append(vm.sandbox_id)
            result=await vm.commands.run(f'printf concurrent-{index}',user='root')
            return {'index':index,'passed':result.stdout==f'concurrent-{index}','wall_s':time.perf_counter()-t}
        finally:
            if vm:await vm.kill()
    report['concurrent']=await asyncio.gather(*(one(i) for i in range(4)))
    report['checks']['concurrent_4']=all(x['passed'] for x in report['concurrent'])
    report['success']=all(report['checks'].values());report['finished_unix']=time.time()
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    assert report['success'],report['checks']

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--env-file',default='/lab/secrets/local-e2b/container.env');p.add_argument('--output',required=True,type=Path)
    asyncio.run(main(p.parse_args()))
