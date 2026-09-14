import hashlib, json, random
from pathlib import Path
from collections import defaultdict
from datasets import load_dataset
import uni_agent.tasks.swe_bench.preprocess as upstream
ROOT=Path('/lab/results/data'); ROOT.mkdir(parents=True,exist_ok=True)
REV='c104f840cc67f8b6eec6f759ebc8b2693d585d4a'
real_load=upstream.load_dataset
upstream.load_dataset=lambda *a,**kw: real_load(*a,revision=REV,**kw)
ds=upstream.build_swe_bench_verified()
ds.to_parquet(str(ROOT/'verified-all.parquet'))
rows=ds.to_list(); byid={r['extra_info']['tools_kwargs']['task']['metadata']['instance_id']:i for i,r in enumerate(rows)}
pilot=['pallets__flask-5014','psf__requests-6028','pytest-dev__pytest-5262','pytest-dev__pytest-7432','pytest-dev__pytest-7521','pytest-dev__pytest-7982']
ds.select([byid[x] for x in pilot]).to_parquet(str(ROOT/'pilot-six.parquet'))
groups=defaultdict(list)
for iid,i in byid.items(): groups[rows[i]['extra_info']['tools_kwargs']['task']['metadata']['repo']].append(iid)
# Prespecified stratified sample: round-robin over all repositories, pseudorandom order per repo.
rng=random.Random(20260912)
for repo in sorted(groups): groups[repo].sort(); rng.shuffle(groups[repo])
selected=[]
while len(selected)<30:
 for repo in sorted(groups):
  if groups[repo] and len(selected)<30: selected.append(groups[repo].pop())
ds.select([byid[x] for x in selected]).to_parquet(str(ROOT/'stratified-thirty.parquet'))
manifest={'dataset':'princeton-nlp/SWE-bench_Verified','revision':REV,'seed':20260912,'policy':'Sorted repositories; sorted instance IDs then random.Random(seed).shuffle per repository; round-robin pop until 30. Chosen before model outcomes. Balanced coverage, not population-representative.','pilot_six':pilot,'stratified_thirty':selected,'dataset_rows':len(rows)}
manifest['files']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('*.parquet')}
(ROOT/'manifest.json').write_text(json.dumps(manifest,indent=2))
images=[]
for iid in dict.fromkeys(pilot+selected): images.append({'instance_id':iid,'image':rows[byid[iid]]['extra_info']['tools_kwargs']['task']['sandbox']['image']})
(ROOT/'images.json').write_text(json.dumps(images,indent=2))
print(json.dumps(manifest,indent=2))
