"""Check that sandbox state survives a lifecycle API Deployment restart."""
import argparse,asyncio,json,time
from pathlib import Path
from provider import SDK,NativeShell,OpenSandboxProvider,settings

async def main(args):
    if args.phase=='create':
        p=OpenSandboxProvider('ubuntu:24.04',timeout=600,cpu='250m',memory='256Mi',instance_label='ua-opensandbox-restart-probe')
        await p.start();shell=await p.open_shell(cwd='/tmp',env={'UA_SURVIVES':'yes'})
        await p.write_file('/tmp/ua-restart-state',b'persisted\n')
        await shell.run('mkdir -p /tmp/ua-restart-dir; cd /tmp/ua-restart-dir')
        args.output.write_text(json.dumps({'sandbox_id':p.sb.id,'session_id':shell.session_id,'created_unix':time.time()},indent=2)+'\n')
        await p.sb.close()  # close only SDK transports; intentionally retain the sandbox
        print('Restart fixture created; SDK transport closed without destroying sandbox')
    else:
        record=json.loads(args.output.read_text());p=OpenSandboxProvider('ubuntu:24.04')
        p.sb=await SDK.connect(record['sandbox_id'],connection_config=settings())
        shell=NativeShell(p,record['session_id'])
        try:
            r=await shell.run('printf "%s:%s" "$PWD" "$UA_SURVIVES"')
            record['session_survived']=r.exit_code==0 and r.stdout=='/tmp/ua-restart-dir:yes'
            record['file_survived']=await p.read_file('/tmp/ua-restart-state')==b'persisted\n'
            record['success']=record['session_survived'] and record['file_survived']
        finally:
            await p.stop();record['cleanup_completed']=True;record['checked_unix']=time.time()
            args.output.write_text(json.dumps(record,indent=2)+'\n')
        print(json.dumps(record));assert record['success']

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['create','check'],required=True);p.add_argument('--output',type=Path,required=True);asyncio.run(main(p.parse_args()))
