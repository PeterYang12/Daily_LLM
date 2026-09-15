"""Build only this experiment's E2B templates from the same pinned SWE images."""
import argparse,asyncio,dataclasses,json,os,sys,time
from pathlib import Path
sys.path.insert(0,'/lab/scripts')
from e2b import AsyncTemplate
import httpx
from run_e2b_e2e import load_environment

async def main(root):
    load_environment('/lab/secrets/e2b.env')
    manifest=json.loads((root/'manifest.json').read_text())
    existing=json.loads((root/'preflight/native-template.json').read_text())
    records=[]
    for index,task in enumerate(manifest['tasks']):
        started=time.monotonic()
        record={'instance_id':task['instance_id'],'image_digest':task['image_digest'],'cpu_count':4,'memory_mb':8192}
        alias='ua-sweprof-'+root.name.split('-')[-1].lower()+'-'+str(index)
        record['alias']=alias
        try:
            if task['instance_id']=='pallets__flask-5014':
                info=existing['build'];record['alias']=existing['alias']
            else:
                built=await AsyncTemplate.build_in_background(AsyncTemplate().from_image(task['image_digest']),name=alias,cpu_count=4,memory_mb=8192,request_timeout=60)
                info=dataclasses.asdict(built)
            record['build']=info
            deadline=time.monotonic()+1800
            async with httpx.AsyncClient(timeout=60,headers={'X-API-Key':os.environ['E2B_API_KEY']}) as client:
                while True:
                    url=os.environ['E2B_API_URL'].rstrip('/')+f"/templates/{info['template_id']}/builds/{info['build_id']}/status"
                    response=await client.get(url);response.raise_for_status();status=response.json()
                    record['status']=status.get('status');record['last_status']=status
                    if record['status']=='ready':break
                    if record['status'] in {'error','failed'}:raise RuntimeError(str(status))
                    if time.monotonic()>deadline:raise TimeoutError('Template readiness deadline exceeded')
                    await asyncio.sleep(5)
        except Exception as exc:
            record['error']=str(exc).replace(os.environ['E2B_API_KEY'],'[REDACTED]')
        record['elapsed_s']=time.monotonic()-started;records.append(record)
        (root/'templates.json').write_text(json.dumps(records,indent=2)+'\n')
        print(json.dumps({k:record.get(k) for k in ['instance_id','status','elapsed_s','error']}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    asyncio.run(main(p.parse_args().root))
