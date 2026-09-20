"""Test provider semantics with real Kubernetes sandboxes, including cancellation."""
import argparse,asyncio,json,sys,time
from pathlib import Path
from provider import OpenSandboxProvider

async def main(args):
    report={'started_unix':time.time(),'checks':{},'sandboxes':[]};sb=OpenSandboxProvider('ubuntu:24.04',timeout=300,cpu='500m',memory='512Mi',instance_label='ua-opensandbox-smoke')
    try:
        await sb.start();report['sandboxes'].append(sb.sb.id)
        r=await sb.exec(['bash','-c','printf "first\\nsecond\\n"; printf "err\\n" >&2; exit 7'])
        report['checks']['exact_output_and_exit']=r.exit_code==7 and r.stdout=='first\nsecond\n' and r.stderr=='err\n'
        report['raw_output_check']={'exit_code':r.exit_code,'stdout':r.stdout,'stderr':r.stderr}
        r=await sb.exec(['bash','-c',"printf '\\n\\n'; head -c 100000 /dev/zero | tr '\\0' x; printf '\\n'"],timeout=30)
        report['checks']['large_output_and_blank_lines']=r.exit_code==0 and r.stdout=='\n\n'+'x'*100000+'\n'
        r=await sb.exec(['bash','-c','printf "%s:%s" "$PWD" "$UA_TEST"'],workdir='/tmp',env={'UA_TEST':'ok'})
        report['checks']['cwd_env']=r.stdout=='/tmp:ok' and r.exit_code==0
        await sb.write_file('/tmp/ua-text','hello\n');report['checks']['text_file']=await sb.read_file('/tmp/ua-text')==b'hello\n'
        data=bytes(range(256))*4096
        await sb.write_file('/tmp/ua-binary',data);report['checks']['binary_1MiB']=await sb.read_file('/tmp/ua-binary')==data
        shell=await sb.open_shell(cwd='/tmp',env={'UA_STATE':'retained'})
        await shell.run('mkdir -p /tmp/ua-state; cd /tmp/ua-state; export UA_EXTRA=kept')
        r=await shell.run('printf "%s:%s:%s" "$PWD" "$UA_STATE" "$UA_EXTRA"')
        report['checks']['persistent_shell']=r.exit_code==0 and r.stdout=='/tmp/ua-state:retained:kept'
        r=await shell.run("sh -c 'exit 7'");report['checks']['session_nonzero']=r.exit_code==7
        r=await sb.exec(['bash','-c','sleep 2; touch /tmp/ua-late-timeout'],timeout=.3)
        report['timeout_result']={'code':r.exit_code,'stderr':r.stderr}
        task=asyncio.create_task(sb.exec(['bash','-c','sleep 2; touch /tmp/ua-late-cancel'],timeout=10))
        await asyncio.sleep(.3);task.cancel()
        try:await task
        except asyncio.CancelledError:report['checks']['cancel_propagated']=True
        await asyncio.sleep(2.1)
        a=await sb.exec(['test','-e','/tmp/ua-late-timeout']);b=await sb.exec(['test','-e','/tmp/ua-late-cancel'])
        report['checks']['timeout_stops_command']=r.exit_code!=0 and a.exit_code==1
        report['checks']['cancel_stops_command']=b.exit_code==1
        await shell.close();await shell.close()
        report['checks']['shell_close_idempotent']=True
    finally:
        await sb.stop();await sb.stop();report['checks']['stop_idempotent']=True
    async def one(index):
        item=OpenSandboxProvider('ubuntu:24.04',timeout=180,cpu='250m',memory='256Mi',instance_label='ua-opensandbox-concurrent')
        try:
            await item.start();report['sandboxes'].append(item.sb.id)
            r=await item.exec(['printf',f'concurrent-{index}']);return r.exit_code==0 and r.stdout==f'concurrent-{index}'
        finally:await item.stop()
    report['checks']['concurrent_4']=all(await asyncio.gather(*(one(i) for i in range(4))))
    report['success']=all(report['checks'].values());report['finished_unix']=time.time()
    args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));assert report['success'],report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);asyncio.run(main(p.parse_args()))
