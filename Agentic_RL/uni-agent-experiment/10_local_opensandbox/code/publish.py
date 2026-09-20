"""Publish OpenSandbox deployment and full-task evidence, excluding credentials."""
import hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TARGET=Path('/home/yuhanya/Daily_LLM/Agentic_RL/uni-agent-experiment/10_local_opensandbox')
assert json.loads((ROOT/'results/deployment-audit.json').read_text())['success']
records=[]
def copy(source,dest):
    output=TARGET/dest;output.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,output)
    records.append({'source':str(source),'destination':dest,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()})
for name in ['README.md','STEPS.md']:copy(ROOT/'report-drafts'/name,name)
for p in (ROOT/'code').glob('*'):
    if p.is_file():copy(p,'code/'+p.name)
for p in (ROOT/'results/executed-code').glob('*.py'):copy(p,'code/executed/'+p.name)
for name in ['values.yaml','api-service.yaml','batchsandbox-template.yaml','domain.xml','ua-opensandbox-local-tunnel.service']:copy(ROOT/'configs'/name,'configs/'+name)
for p in (ROOT/'figures').glob('*'):
    if p.suffix in {'.svg','.mmd'}:copy(p,'figures/'+p.name)
for name in ['deployment-audit.json','provider-smoke-final.json','restart-probe.json','pods-before-api-restart.json','pods-after-api-restart.json','model-service.json','executed-code-hashes.json']:copy(ROOT/'results'/name,'results/'+name)
for name in ['provenance.json','cloud-image-provenance.json','k3s-provenance.json']:copy(ROOT/'downloads'/name,'results/'+name)
copy(ROOT/'state.json','results/runtime-state.json')
base=ROOT/'results/uni-agent-flask';copy(base/'manifest.json','configs/swe-manifest.json')
case=base/'react/opensandbox/pallets__flask-5014'
for name in ['result.json','input-config.json','verifier.json','verifier-raw.log','trajectory-audit.json','candidate-source-only.patch']:copy(case/name,'results/uni-agent/'+name)
for p in case.glob('sessions/*/trajectories.jsonl'):copy(p,'results/uni-agent/trajectories.jsonl')
for mode in ['baseline','oracle']:copy(base/'controls/opensandbox/pallets__flask-5014'/mode/'result.json','results/controls/'+mode+'.json')
(TARGET/'results/source-manifest.json').write_text(json.dumps({'runtime':str(ROOT),'copied_files':records},indent=2)+'\n')
print(json.dumps({'published':str(TARGET),'artifacts':len(records)}))
