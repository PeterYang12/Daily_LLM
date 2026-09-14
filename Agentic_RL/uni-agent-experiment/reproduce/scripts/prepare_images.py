import concurrent.futures, json, subprocess, time, argparse
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--locked',action='store_true');args=parser.parse_args()
root=Path('/lab/results/data'); logdir=Path('/lab/logs/images'+('-replay-'+str(int(time.time())) if args.locked else '')); logdir.mkdir(exist_ok=True)
rows=json.loads((root/('image-preparation.json' if args.locked else 'images.json')).read_text())
if args.locked:
 assert all(r.get('repo_digests') and r.get('returncode')==0 for r in rows), 'Locked image manifest must contain successful digests'
def pull(r):
 start=time.time(); image=r['image']; path=logdir/(r['instance_id']+'.log')
 with path.open('w') as f: p=subprocess.run(['docker','pull',r['repo_digests'][0] if args.locked else image],stdout=f,stderr=subprocess.STDOUT,timeout=1800)
 result={**r,'returncode':p.returncode,'elapsed_seconds':time.time()-start}
 if p.returncode==0:
  if args.locked: subprocess.run(['docker','tag',r['repo_digests'][0],image],check=True)
  d=json.loads(subprocess.check_output(['docker','image','inspect',image]))[0]
  result.update(image_id=d['Id'],repo_digests=d.get('RepoDigests'),size_bytes=d['Size'])
 (logdir/(r['instance_id']+'.json')).write_text(json.dumps(result,indent=2)); print(json.dumps(result),flush=True)
 return result
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
 results=list(ex.map(pull,rows))
(root/('image-preparation-replay-'+str(int(time.time()))+'.json' if args.locked else 'image-preparation.json')).write_text(json.dumps(results,indent=2))
