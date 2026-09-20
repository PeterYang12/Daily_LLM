"""Freeze the qualified train/evaluation split before the 300-step run."""
import argparse,hashlib,json,shutil
from pathlib import Path
from datasets import Dataset
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--min-train',type=int,default=64);p.add_argument('--min-val',type=int,default=16);a=p.parse_args()
assert not (ROOT/'data/frozen-manifest.json').exists(),'The main data selection is already frozen'
progress=json.loads((ROOT/'results/qualification-progress.json').read_text())
assert progress['counts']['training']>=a.min_train and progress['counts']['validation']>=a.min_val,progress['counts']
rows={}
for split,target in [('training','train'),('validation','val')]:
    source=ROOT/'data'/(split+'-qualified.parquet');dest=ROOT/'data'/(target+'.parquet')
    dataset=Dataset.from_parquet(str(source))
    count=a.min_train if split=='training' else a.min_val
    dataset=dataset.select(range(count));dataset.to_parquet(str(dest))
    ids=[r['extra_info']['tools_kwargs']['task']['metadata']['instance_id'] for r in dataset]
    assert len(ids)==len(set(ids))
    rows[target]={'n':len(ids),'instance_ids':ids,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}
assert not set(rows['train']['instance_ids'])&set(rows['val']['instance_ids'])
(ROOT/'data/frozen-manifest.json').write_text(json.dumps(rows,indent=2)+'\n')
print({k:v['n'] for k,v in rows.items()})
