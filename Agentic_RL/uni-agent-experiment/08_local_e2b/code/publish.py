"""Publish local E2B acceptance evidence without installed images or credentials."""
import hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DOCS=Path('/home/yuhanya/Daily_LLM/Agentic_RL/uni-agent-experiment/08_local_e2b')
audit=json.loads((ROOT/'results/deployment-audit.json').read_text());assert audit['success']
records=[]
def copy(source,relative):
    source=Path(source);dest=DOCS/relative;dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,dest)
    records.append({'source':str(source),'destination':relative,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()})
for name in ['README.md','STEPS.md']:copy(ROOT/'report-drafts'/name,name)
for p in (ROOT/'code').glob('*'):
    if p.is_file():copy(p,'code/'+p.name)
for p in (ROOT/'figures').glob('*'):
    if p.suffix in {'.mmd','.svg'}:copy(p,'figures/'+p.name)
for p in (ROOT/'configs/deploy').rglob('*'):
    if p.is_file() and p.suffix in {'.yaml','.json','.xml','.toml','.mjs'}:copy(p,'configs/deploy/'+str(p.relative_to(ROOT/'configs/deploy')))
for name in ['domain.xml','ua-e2b-local-tunnel.service']:copy(ROOT/'configs'/name,'configs/'+name)
for name in ['sdk-smoke-before-restart.json','sdk-smoke-after-restart.json','deployment-audit.json','network-probe.json','base-rebuild.json','test-model.json']:
    copy(ROOT/'results'/name,'results/'+name)
copy(ROOT/'downloads/cloud-image-provenance.json','results/cloud-image-provenance.json')
copy(ROOT/'state.json','results/runtime-state.json')
base=ROOT/'results/uni-agent-flask'
copy(base/'manifest.json','configs/swe-manifest.json');copy(base/'templates.json','results/swe-templates.json')
case=base/'react-local/e2b/pallets__flask-5014'
for name in ['result.json','input-config.json','verifier.json','trajectory-audit.json','candidate-source-only.patch','verifier-raw.log']:
    copy(case/name,'results/uni-agent/'+name)
for p in case.glob('sessions/*/trajectories.jsonl'):copy(p,'results/uni-agent/trajectories.jsonl')
for mode in ['baseline','oracle']:
    copy(base/'controls-v2/e2b/pallets__flask-5014'/mode/'result.json','results/controls/'+mode+'.json')
(DOCS/'results/source-manifest.json').write_text(json.dumps({'runtime':str(ROOT),'copied_files':records},indent=2)+'\n')
print(json.dumps({'published':str(DOCS),'copied_files':len(records)}))
