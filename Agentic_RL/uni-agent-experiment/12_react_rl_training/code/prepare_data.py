"""Normalize the recommended training corpus and fix disjoint task candidates."""
import collections,hashlib,json,random,sys
from pathlib import Path
from datasets import Dataset,load_dataset
from huggingface_hub import HfApi
sys.path.insert(0,str(Path(__file__).resolve().parent))
from ua_rl.common import ROOT,save

original=Dataset.from_parquet(str(ROOT/'data/recommended-train-original.parquet'))
validation=load_dataset('princeton-nlp/SWE-bench_Verified',split='test')
validation.to_parquet(str(ROOT/'data/verified-original.parquet'))
validation_ids=set(validation['instance_id'])
rows=[]
for row in original:
    task=row['extra_info']['tools_kwargs']['task'];meta=task['metadata'];iid=meta['instance_id']
    if iid in validation_ids:continue
    if not meta.get('FAIL_TO_PASS') or meta.get('log_parser') not in ['parse_log_pytest','parse_log_pytest_v2']:continue
    # Fixed structural filter, not a model-success filter. Keep practical Python jobs.
    if len(meta['problem_statement'])>16000 or len(meta.get('PASS_TO_PASS') or [])>400:continue
    clean={'name':'swe_rebench','metadata':meta,'sandbox':{'image':f"swerebench/sweb.eval.x86_64.{iid.lower().replace('__','_1776_')}"}}
    rows.append({'instance_id':iid,'family':'swe_rebench','image':clean['sandbox']['image'],'row':{'data_source':'nebius/SWE-rebench','prompt':[{'role':'user','content':meta['problem_statement']}],'extra_info':{'tools_kwargs':{'task':clean}}}})
rng=random.Random(20260916);rng.shuffle(rows)
# Interleave repositories to keep the bounded template collection diverse.
groups=collections.defaultdict(list)
for row in rows:groups[row['row']['extra_info']['tools_kwargs']['task']['metadata']['repo']].append(row)
selected=[]
while groups and len(selected)<160:
    for repo in list(groups):
        selected.append(groups[repo].pop())
        if not groups[repo]:del groups[repo]
        if len(selected)>=160:break
# Seed the holdout with the already exercised Flask case, then select candidates.
previous=Path('/lab/local-e2b-20260916/results/uni-agent-flask/manifest.json')
flask=json.loads(previous.read_text())['tasks'][0]
eval_rows=[{'instance_id':flask['instance_id'],'family':'swe_bench','image':flask['image_digest'],'row':flask['row']}]
preferred=['pytest-dev/pytest','pallets/flask','psf/requests','sphinx-doc/sphinx','pydata/xarray','sympy/sympy','django/django','scikit-learn/scikit-learn']
val_candidates=[r for r in validation if r['repo'] in preferred and r['instance_id']!=flask['instance_id'] and len(r['problem_statement'])<12000]
rng.shuffle(val_candidates)
counts=collections.Counter()
for r in val_candidates:
    if counts[r['repo']]>=3:continue
    counts[r['repo']]+=1;iid=r['instance_id']
    meta={k:r[k] for k in ['instance_id','repo','base_commit','patch','test_patch','problem_statement','FAIL_TO_PASS','PASS_TO_PASS','version']}
    task={'name':'swe_bench','metadata':meta,'sandbox':{'image':f"swebench/sweb.eval.x86_64.{iid.lower().replace('__','_1776_')}"}}
    eval_rows.append({'instance_id':iid,'family':'swe_bench','image':task['sandbox']['image'],'row':{'data_source':'princeton-nlp/SWE-bench_Verified','prompt':[{'role':'user','content':meta['problem_statement']}],'extra_info':{'tools_kwargs':{'task':task}}}})
    if len(eval_rows)==24:break
assert not ({r['instance_id'] for r in selected}&{r['instance_id'] for r in eval_rows})
save(ROOT/'data/candidates.json',{'seed':20260916,'training':selected,'validation':eval_rows,'target_qualified_training':128,'target_qualified_validation':16})
api=HfApi()
save(ROOT/'results/dataset-provenance.json',{'training_dataset':'dyyyyyyyy/swe-rebench-filtered-1150','training_revision':api.dataset_info('dyyyyyyyy/swe-rebench-filtered-1150').sha,'validation_dataset':'princeton-nlp/SWE-bench_Verified','validation_revision':api.dataset_info('princeton-nlp/SWE-bench_Verified').sha,'original_training_rows':len(original),'eligible_training_rows':len(rows),'training_candidates':len(selected),'validation_candidates':len(eval_rows),'training_repositories':len({r['row']['extra_info']['tools_kwargs']['task']['metadata']['repo'] for r in selected}),'split_overlap':0,'selection':'Seeded repository-interleaved structural selection, then template and baseline/oracle qualification; no model-success filtering'})
print('Fixed candidates:',len(selected),'training;',len(eval_rows),'validation')
