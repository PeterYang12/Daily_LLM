"""Copy allowlisted experiment evidence; never reads credentials, weights, or runtime caches."""
import argparse,hashlib,json,shutil,re
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--lab-root',type=Path,default=Path('/home/yuhanya/uni-agent-lab'));a=p.parse_args()
LAB=a.lab_root;ROOT=Path(__file__).resolve().parents[1];records=[]
def write(dest,data):
 q=ROOT/dest;q.parent.mkdir(parents=True,exist_ok=True);q.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def cp(src,dest):
 p=LAB/src;q=ROOT/dest;q.parent.mkdir(parents=True,exist_ok=True);data=p.read_bytes()
 # The remote endpoint is user supplied; retain the API behavior without publishing its address.
 cleaned=re.sub(rb'https?://(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?::[0-9]+)?/e2b',b'[user-provided E2B endpoint]',data);q.write_bytes(cleaned)
 records.append({'source':src,'destination':dest,'source_sha256':hashlib.sha256(data).hexdigest(),'destination_sha256':hashlib.sha256(cleaned).hexdigest(),'bytes':len(cleaned),'transformation':'endpoint redacted' if cleaned!=data else 'byte-for-byte copy'})
cp('results/final-summary.json','evidence/summary/final-summary.json')
cp('results/per-case-results.csv','results/per-case-results.csv')
for n in ['manifest','validated-manifest','image-preparation']:
 cp('results/data/'+n+'.json','evidence/summary/'+n+'.json')
for n in ['official-api-react','official-api-claude','official-api-react-aiter-fixed','aiter-react-swe-smoke','pilot-baseline-dns','pilot-oracle-dns','pilot-react-gateway','pilot-claude-gateway','pilot-mini-gateway','thirty-baseline-dns','thirty-oracle-dns','trajectory-logprobs-react','trajectory-logprobs-claude','trajectory-logprobs-mini','xarray-oracle-32g']:
 d=json.loads((LAB/'results'/n/'summary.json').read_text());summary=d.get('summary',{k:v for k,v in d.items() if k!='results'})
 write('evidence/summary/'+n+'.json',{'source':'results/'+n+'/summary.json','summary':summary,'per_case':[ {k:r.get(k) for k in ['instance_id','reward','resolved','eval_completed','finished','wall_seconds','agent_seconds','error','agent_error'] if k in r} for r in d.get('results',[])]})
for n in ['serving-benchmark','serving-aiter-benchmark','serving-replicas-benchmark-v2']:
 for f in ['summary.json','raw.json']:cp('results/'+n+'/'+f,'evidence/performance/'+n+'/'+f)
for n in ['docker','e2b']:
 for f in ['result.json','baseline.json','verifier.json','interval_utils.py','environment.txt']:
  cp('results/sandbox-agent/'+n+'/'+f,'evidence/sandbox/'+n+'/'+f)
for n,dest in [('results/sandbox-checks-v3/summary.json','docker-isolation.json'),('results/janitor-validation.json','janitor-validation.json'),('results/e2b/sdk-smoke-v2.json','e2b-sdk-smoke.json'),('results/final-service-audit.json','service-audit-20260912.json')]:cp(n,'evidence/sandbox/'+dest)
for label,folder in [('eager','model-smoke'),('aiter','model-smoke-aiter')]:
 for f in ['chat.json','tool.json','anthropic.json','token_ids.json']:cp('results/'+folder+'/'+f,'evidence/summary/model-api-'+label+'/'+f)
cp('logs/sandbox-official-demo-attempt-02.log','evidence/sandbox/docker-official-demo.txt')
cp('logs/e2b-official-demo-attempt-02.log','evidence/sandbox/e2b-official-demo.txt')
cp('results/trajectory-audit.json','evidence/trajectories/audit.json')
for n in ['runtime.json','model-files.json']:cp('results/provenance/'+n,'evidence/provenance/'+n)
for n in ['image-digests.txt','gpu-preflight.txt','host-lscpu.txt','host-uname.txt','sandbox-disk-usage.txt','logging-fork-before-v2.json','logging-fork-after.json','delivery-checks.json']:
 cp('results/'+n,'evidence/provenance/'+n)
cp('deliverables/verification.json','evidence/provenance/reproduction-kit-verification.json')
for n in ['reproduction-cold-check.log','reproduction-cold-patch-check.log','logging-tests.log']:
 # Keep only final acceptance lines, avoiding dependency download noise.
 text=(LAB/'logs'/n).read_text();write('evidence/provenance/'+n+'.json',{'source':'logs/'+n,'last_lines':text.splitlines()[-12:]})
selected={'pallets__flask-5014','pydata__xarray-4075','sphinx-doc__sphinx-9367','pytest-dev__pytest-7521','django__django-14122'}
for mode in ['react','claude','mini']:
 base=LAB/'results'/('regraded-main-'+mode)
 for src in sorted(base.glob('*/result.json')):
  d=json.loads(src.read_text());slim={k:v for k,v in d.items() if k not in ['eval_report','agent_info']}
  slim['source_artifact']=str(src.relative_to(LAB));slim['source_sha256']=hashlib.sha256(src.read_bytes()).hexdigest()
  tests=(d.get('eval_report') or {}).get('test_status') or {}
  slim['test_summary']={k:{'passed':len(v.get('success',[])),'failed':len(v.get('failure',[])),'failed_tests':v.get('failure',[])} for k,v in tests.items()}
  write('evidence/cases/'+mode+'/'+src.parent.name+'/result.json',slim)
  if src.parent.name in selected:
   for f in ['candidate-delta.patch','candidate-source-only.patch']:
    cp(str((src.parent/f).relative_to(LAB)),'evidence/cases/'+mode+'/'+src.parent.name+'/'+f)
# Record exactly the driver used for the original main experiment and the latest replay helpers separately.
for f in ['run_swe.py','lab_runtime.py']:
 cp('results/main-react-128k/runner-source/'+f,'reproduce/scripts/executed/'+f)
for f in ['labctl.py','status.py','check_logging_fork.py','e2b_smoke.py','bootstrap_locked.sh','download_model.py','prepare_data.py','prepare_images.py','build_tmux_runtime.py','build_mini_runtime.sh','run_swe.py','lab_runtime.py','regrade_swe.py','e2b_provider.py','run_sandbox_agent.py','run_e2b_demo.py','sandbox_checks.py','sandbox_janitor.py','benchmark_serving.py','benchmark_aiter.py','benchmark_replicas_v2.py','serve_model.sh','serve_model_128k.sh','serve_model_tp4.sh','serve_model_aiter.sh','audit_trajectories.py']:
 cp('scripts/'+f,'reproduce/scripts/'+f)
for f in sorted((LAB/'configs').iterdir()):
 if f.is_file():cp(str(f.relative_to(LAB)),'reproduce/configs/'+f.name)
cp('patches/uni-agent-logging-fork.patch','reproduce/patches/uni-agent-logging-fork.patch')
community=[]
for f in sorted((LAB/'results/community').glob('*.json')):
 d=json.loads(f.read_text())
 if isinstance(d,dict) and d.get('full_name'):
  community.append({k:d.get(k) for k in ['full_name','html_url','description','stargazers_count','forks_count','pushed_at','default_branch','license']})
write('evidence/summary/community-snapshot-20260912.json',community)
write('evidence/source-manifest.json',{'experiment_date':'2026-09-12','documentation_date':'2026-09-14','original_lab_root':str(LAB),'copied_files':records,'derived_files':'Slim per-case records and extracted summaries state their source; scores unchanged. Credentials, weights, full logs and large token arrays are excluded.'})
print('Copied',len(records),'files; wrote 84 compact case records and summary extractions.')
