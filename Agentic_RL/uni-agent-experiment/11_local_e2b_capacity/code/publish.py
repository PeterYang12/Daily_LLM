"""Publish migration and capacity evidence; exclude private state and disks."""
import hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DOCS=Path('/home/yuhanya/Daily_LLM/Agentic_RL/uni-agent-experiment/11_local_e2b_capacity')
assert json.loads((ROOT/'results/deployment-audit.json').read_text())['success']
records=[]
def copy(source,relative):
    source=Path(source);dest=DOCS/relative;dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,dest)
    records.append({'source':str(source),'destination':relative,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()})
for name in ['README.md','STEPS.md']:copy(ROOT/'report-drafts'/name,name)
for p in (ROOT/'code').glob('*'):
    if p.suffix in {'.py','.sh'}:copy(p,'code/'+p.name)
for p in (ROOT/'figures').glob('*'):
    if p.suffix in {'.mmd','.svg'}:copy(p,'figures/'+p.name)
for p in (ROOT/'configs').rglob('*'):
    if p.is_file() and (p.suffix in {'.yaml','.json','.xml','.toml','.mjs','.sql','.service'} or p.name=='ssh_config'):
        copy(p,'configs/'+str(p.relative_to(ROOT/'configs')))
for p in (ROOT/'results').rglob('*'):
    if p.is_file() and p.suffix in {'.json','.jsonl','.log','.txt','.patch'}:
        copy(p,'results/'+str(p.relative_to(ROOT/'results')))
copy(ROOT/'state.json','results/runtime-state.json')
copy(ROOT.parent/'local-e2b-20260916/downloads/cloud-image-provenance.json','results/cloud-image-provenance.json')
(DOCS/'results/source-manifest.json').write_text(json.dumps({'runtime':str(ROOT),'copied_files':records},indent=2)+'\n')
print(json.dumps({'published':str(DOCS),'copied_files':len(records)}))
