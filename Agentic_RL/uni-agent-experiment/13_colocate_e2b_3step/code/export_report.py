import hashlib
import json
import shutil
from pathlib import Path
from runtime_common import ROOT

dest = ROOT/'public-report'
dest.mkdir(exist_ok=True)
for name in ['README.md']:
    shutil.copy2(ROOT/name, dest/name)
for directory in ['code','configs','figures']:
    shutil.copytree(ROOT/directory, dest/directory, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__'))
selected = ['failure-summary.json','kernel-fault-summary.txt','kernel-fault.log','process-fault-excerpt.txt',
            'train-metrics.jsonl','trajectory-audit.json','capacity-128.json','capacity-128-attempt-1.json',
            'qualification-summary.json','task-config-check.json','data-preparation.json',
            'environment.json','container-image.json','cleanup.json','local-source-changes.patch',
            'implementation-decisions.json','tool-provision.json','ray-gpu-mapping.json',
            'template-import-probe.json','template-import-probe-status.json','main-exit.json']
(dest/'results').mkdir(exist_ok=True)
for name in selected:
    if (ROOT/'results'/name).exists():
        shutil.copy2(ROOT/'results'/name, dest/'results'/name)
for directory in ['optimizer','gpu-health','gpu-mesh']:
    shutil.copytree(ROOT/'results'/directory,dest/'results'/directory,dirs_exist_ok=True)
(dest/'configs/data').mkdir(exist_ok=True)
for name in ['scheduled-tasks.json','reusable-templates.json']:
    shutil.copy2(ROOT/'data'/name,dest/'configs/data'/name)
(dest/'STEPS.md').write_text('''This run stopped after one completed update because the kernel reported an uncorrectable ATHUB event and reset all eight GPUs. The machine's GPU/interconnect/driver condition needs investigation before another full training attempt.

1. Review `results/kernel-fault-summary.txt` and the full kernel window. The reported devices are PCI 0000:66:00.0 (uncorrectable ATHUB) and 0000:96:00.0 (correctable XGMI events). No manual reset or host reboot was performed by this experiment.
2. Preserve this stopped run. For a later attempt, create a new run directory/run ID and update the explicit runtime paths in the scripts/configuration. Start from the original Qwen3-Coder-30B-A3B-Instruct weights: this run did not reach its step-3 checkpoint save.
3. Recheck the actual imported Uni-Agent/verl paths and the pinned container environment, GPU math/communication, CP4/EP8 topology, Ray GPU mapping, E2B lifecycle/capacity, and task base/reference controls after the machine is repaired.
4. Review `configs/train.yaml` and `configs/task_config.yaml`. The target remains three steps, batch 16, n=8, concurrency 128, 128K and 200 turns. The E2B lifetime is 3,600 seconds because of the server limit. The complete 1,150-row training file was kept, but ready tasks were ordered first and warmup prefetch was disabled for this bounded run.
5. The original runtime entry is `bash /work/experiments/colocate-e2b-3step-20260920/code/launch.sh` inside container `yuhan-uniagent`. Use its `--cfg job --resolve` mode to inspect configuration without starting training. Do not overwrite this run's metrics and evidence when creating a new attempt.
6. `code/status.py` reads status. `code/cleanup.py` deletes only E2B instances tagged with the matching run ID. `code/audit_trajectories.py` checks exported token data. `code/verify_checkpoint.py` and `code/final_report.py` were prepared for successful completion but were not run because no step-3 checkpoint exists.

Raw data, source checkout, full per-session logs and trajectories are retained at `/home/yuhanya/yuhan-uniAgent/experiments/colocate-e2b-3step-20260920` on the host, mounted at the corresponding `/work/experiments/...` path in the container. The report bundle excludes secrets, weights and installed environments.
''')
hashes={}
for p in dest.rglob('*'):
    if p.is_file() and p != dest/'results/report-manifest.json':
        hashes[str(p.relative_to(dest))]={'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
(dest/'results/report-manifest.json').write_text(json.dumps(hashes,indent=2))
print(json.dumps({'report_directory':str(dest),'files':len(hashes),'bytes':sum(x['bytes'] for x in hashes.values())}))
