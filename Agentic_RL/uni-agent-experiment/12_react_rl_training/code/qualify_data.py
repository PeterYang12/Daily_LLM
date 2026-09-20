"""Build bounded, pinned E2B templates and verify baseline/gold before RL."""
import argparse,asyncio,copy,dataclasses,hashlib,json,os,sys,time
from pathlib import Path
import httpx
from datasets import Dataset
from e2b import AsyncTemplate
sys.path.insert(0,str(Path(__file__).resolve().parent))
from ua_rl.common import ROOT,Profile,load_e2b,save
from ua_rl.provider import E2BRL,checked
from ua_rl.tasks import prepare,reward

async def image_digest(client,image):
    if '@sha256:' in image:return image,0
    repository,tag=(image.rsplit(':',1) if ':' in image else (image,'latest'))
    r=await client.get('https://auth.docker.io/token',params={'service':'registry.docker.io','scope':'repository:'+repository+':pull'});r.raise_for_status()
    token=r.json()['token']
    r=await client.get(f'https://registry-1.docker.io/v2/{repository}/manifests/{tag}',headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.index.v1+json'})
    r.raise_for_status();data=r.json()
    if 'manifests' in data:
        digest=next(m['digest'] for m in data['manifests'] if m.get('platform',{}).get('architecture')=='amd64' and m.get('platform',{}).get('os')=='linux')
        r=await client.get(f'https://registry-1.docker.io/v2/{repository}/manifests/{digest}',headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json'});r.raise_for_status();data=r.json()
    return repository+'@'+r.headers['Docker-Content-Digest'],sum(x.get('size',0) for x in data.get('layers',[]))

async def main(args):
    load_e2b();endpoint=os.environ['E2B_API_URL'];key=os.environ['E2B_API_KEY']
    manifest=json.loads((ROOT/'data/candidates.json').read_text())
    jobs=[]
    # First qualify a few training tasks, then validation, then the remaining train set.
    for split,rows in [('training',manifest['training'][:8]),('validation',manifest['validation']),('training',manifest['training'][8:])]:
        jobs.extend((split,r) for r in rows)
    if args.limit:jobs=jobs[:args.limit]
    slots=asyncio.Semaphore(args.concurrency)
    async def one(split,task):
        async with slots:
            progress=ROOT/'results/qualification-progress.json'
            if progress.exists():
                counts=json.loads(progress.read_text())['counts']
                if counts.get(split,0)>={'training':64,'validation':16}[split]:return
            iid=task['instance_id'];out=ROOT/'results/qualification'/iid
            if (out/'summary.json').exists():return
            out.mkdir(parents=True,exist_ok=True);summary={'instance_id':iid,'split':split,'started_unix':time.time(),'qualified':False}
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    digest,size=await image_digest(client,task['image'])
                    summary.update(image_digest=digest,compressed_image_bytes=size)
                    if size>2*1024**3:raise ValueError('Bounded first-run image budget: compressed image exceeds 2 GiB')
                    alias='ua-rl-'+hashlib.sha256(digest.encode()).hexdigest()[:16]
                    build_file=out/'build.json'
                    if build_file.exists():info=json.loads(build_file.read_text())
                    elif iid=='pallets__flask-5014':
                        info={'template_id':'vatm00lmw03h9y86956r','build_id':None,'alias':'local-swe-flask-5014'}
                        save(build_file,info)
                    else:
                        build=await AsyncTemplate.build_in_background(AsyncTemplate().from_image(digest),name=alias,cpu_count=4,memory_mb=8192,request_timeout=60)
                        info=dataclasses.asdict(build);save(build_file,info)
                    summary['template_id']=info['template_id']
                    if info.get('build_id'):
                        deadline=time.monotonic()+1200
                        while True:
                            response=await client.get(endpoint+f"/templates/{info['template_id']}/builds/{info['build_id']}/status",headers={'X-API-Key':key});response.raise_for_status();status=response.json()
                            save(out/'build-status.json',status)
                            if status.get('status')=='ready':break
                            if status.get('status') in ['error','failed']:raise RuntimeError('Template build failed; see build-status.json')
                            if time.monotonic()>deadline:raise TimeoutError('Template build deadline')
                            await asyncio.sleep(4)
                meta=task['row']['extra_info']['tools_kwargs']['task']['metadata']
                for mode in ['baseline','oracle']:
                    p=Profile('qualification-'+iid+'-'+mode,out/mode)
                    with p.active():
                        sb=E2BRL(info['template_id'],1200)
                        try:
                            await sb.__aenter__();await prepare(sb,meta,tools=False)
                            if mode=='oracle':
                                await sb.write_file('/tmp/gold.patch',meta['patch'])
                                await checked(sb,['git','apply','--whitespace=fix','/tmp/gold.patch'],workdir='/testbed')
                            score=await reward(task['family'],meta,sb,timeout=300)
                            save(out/mode/'result.json',score);summary[mode]={k:score[k] for k in ['resolved','eval_completed','eval_execution_time']}
                        finally:await sb.stop();p.save()
                summary['qualified']=summary['baseline']['eval_completed'] and not summary['baseline']['resolved'] and summary['oracle']['eval_completed'] and summary['oracle']['resolved']
            except Exception as exc:summary['error']=(type(exc).__name__+': '+str(exc)).replace(key,'[REDACTED]')
            summary['elapsed_s']=time.time()-summary['started_unix'];save(out/'summary.json',summary)
            print(json.dumps(summary),flush=True)
            export(manifest)
    await asyncio.gather(*(one(split,t) for split,t in jobs))
    export(manifest)

def export(manifest):
    totals={};records={}
    for split,target in [('training',64),('validation',16)]:
        chosen=[];ids=[]
        for task in manifest[split]:
            path=ROOT/'results/qualification'/task['instance_id']/'summary.json'
            if not path.exists():continue
            status=json.loads(path.read_text())
            if not status['qualified']:continue
            row=copy.deepcopy(task['row']);cfg=row['extra_info']['tools_kwargs']['task']
            cfg['name']='rl_'+task['family'];cfg.pop('prompt',None)
            cfg['sandbox']={'provider':'e2b_rl','image':status['template_id'],'runtime_timeout':3600}
            row['prompt']=[{'role':'user','content':cfg['metadata']['problem_statement']}]
            chosen.append(row);ids.append(task['instance_id'])
            if len(chosen)>=target:break
        if chosen:
            Dataset.from_list(chosen).to_parquet(str(ROOT/'data'/(split+'-qualified.parquet')),batch_size=1000)
        totals[split]=len(chosen);records[split]=ids
    save(ROOT/'results/qualification-progress.json',{'counts':totals,'instances':records,'updated_unix':time.time()})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--concurrency',type=int,default=2);p.add_argument('--limit',type=int)
    asyncio.run(main(p.parse_args()))
