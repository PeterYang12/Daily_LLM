"""Audit or remove only running E2B instances owned by the supplied experiment."""
import argparse,asyncio,json,os,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,'/lab/scripts')
import httpx
from e2b import AsyncSandbox
from run_e2b_e2e import load_environment

async def main(args):
    load_environment('/lab/secrets/e2b.env');known=set()
    for pattern in ['*/**/sandbox-*.json','preflight/native-sandbox-id.json','native-shell-probe.json']:
        for path in args.root.glob(pattern):
            try:
                value=json.loads(path.read_text())
                if value.get('sandbox_id'):known.add(value['sandbox_id'])
            except (ValueError,AttributeError):pass
    async with httpx.AsyncClient(timeout=30,headers={'X-API-Key':os.environ['E2B_API_KEY']}) as client:
        async def inventory():
            response=await client.get(os.environ['E2B_API_URL'].rstrip('/')+'/sandboxes');response.raise_for_status()
            data=response.json();return data if isinstance(data,list) else data.get('items',data.get('sandboxes',[]))
        def owned(rows):
            ids=[]
            for row in rows:
                identifier=row.get('sandboxID') or row.get('sandbox_id')
                metadata=row.get('metadata') or {}
                if isinstance(metadata,str):
                    try:metadata=json.loads(metadata)
                    except ValueError:metadata={}
                if identifier in known or metadata.get('experiment')==args.root.name:ids.append(identifier)
            return ids
        before=await inventory();ours=owned(before);deleted=[]
        if args.delete:
            for identifier in ours:
                acknowledged=await AsyncSandbox.kill(identifier,request_timeout=30)
                deleted.append({'sandbox_id':identifier,'kill_ack':acknowledged})
        after=await inventory();remaining=owned(after)
    report={'time_unix':time.time(),'known_created_ids':len(known),'owned_running_before':ours,'deleted':deleted,
            'owned_running_after':remaining,'other_running_count':len(after)-len(remaining),
            'templates':'Retained for replay; no template deletion was requested.'}
    def local_inventory():
        output=subprocess.check_output(['docker','ps','-q','--filter','label=ua-swe-profile='+args.root.name],text=True)
        return output.split()
    local_before=local_inventory();local_deleted=[]
    if args.delete:
        for identifier in local_before:
            subprocess.run(['docker','rm','-f',identifier],check=True,capture_output=True)
            local_deleted.append(identifier)
    report.update(docker_owned_running_before=local_before,docker_deleted=local_deleted,
                  docker_owned_running_after=local_inventory())
    (args.root/'service-audit.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    if args.delete and (remaining or report['docker_owned_running_after']):
        raise RuntimeError('Experiment still has running sandbox instances')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--delete',action='store_true');asyncio.run(main(p.parse_args()))
