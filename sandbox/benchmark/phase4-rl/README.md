# Phase 4: active R2 training and reproducibility

**September 21 update: the user stopped R2 after six completed native steps. Checkpoint 6 is preserved; step 7 was interrupted. The trainer, container, supervisor and collectors have exited.** Read the [stopped-run review](../REVIEW-20260921.md) and [final status](STATUS.md) before using the historical instructions below. The old supervisor resume command assumes checkpoint 1 and must not be reused blindly for checkpoint 6.

The active run is `ua-e2b-pathfinding-20260920-r2`, under `/data2/yuhanya/e2b-rl-pathfinding-20260920-r2`. Its original attempt and reviewed checkpoint-1 resume are already started. Read [STATUS](STATUS.md) for current counts, [REPORT](REPORT.md) for measured results, and [RUN-PLAN](RUN-PLAN.md) for the 62-task training / 16-task evaluation scope.

The [first-batch bottleneck analysis](FIRST-BATCH-ANALYSIS.md) separates earlier startup queueing from the final model-call tail and the native checkpoint cost. The [startup diagnostics](ATTEMPT-2-DIAGNOSTICS.md) trace the observed timeouts, retries and failed-create cleanup.

From the benchmark directory, inspect the existing run with:

```bash
sudo -n python3 -B phase4-rl/status_snapshot.py \
  --root /data2/yuhanya/e2b-rl-pathfinding-20260920-r2
```

This command reads existing records and exits. It starts no trainer, sandbox or collector. Native steps, runner results and device activity remain separate measurements.

## Configuration and code

[The resume config archive](configs/resume-20260920T202452Z-d696dc09/manifest.json) contains `train.yaml`, `task_config.yaml`, `training-plan.json` and `source-provenance.json` from attempt `20260920T202452Z-d696dc09-attempt-01`. The manifest records source paths, SHA-256 hashes and the successful credential-value scan. Verify the copies locally:

```bash
cd phase4-rl/configs/resume-20260920T202452Z-d696dc09
sha256sum --check SHA256SUMS
```

The [runtime link](code/runtime) exposes the active R2 scripts on this host, including configuration construction, supervision, checkpoint inspection and profiling. Its absolute target requires the `/data2` experiment tree. It is not a second source checkout. Source provenance is recorded in the exported JSON; the prepared image, model, dataset/template mapping and private credentials are additional prerequisites for reproducing this experiment on another machine.

The reporting scripts are [publish_report.py](publish_report.py), [analyze_first_batch.py](analyze_first_batch.py) and [status_snapshot.py](status_snapshot.py). The [checkpoint-1 checker](review_checkpoint1_prefix.py) describes the original acceptance boundary; after the reviewed persistence and resume it is historical inspection code, not a command to launch or reaccept the running job. The publisher watcher is already active.

## Large and private artifacts

| Artifact | Host path under `/data2/yuhanya/e2b-rl-pathfinding-20260920-r2` |
|---|---|
| Active configs and dataset/template manifests | `configs/` |
| Runtime training / evaluation inputs | `data/qualified/train.parquet`, `data/qualified/val.parquet` |
| Uni-Agent / verl source | `src/uni-agent/` |
| Native metrics / validation dumps | `results/train-metrics.jsonl`, `results/validation/` |
| Private sessions / trajectories / profiling | `results/sessions/`, `results/trajectories/`, `results/profile/` |
| Resource samples | `results/resources-host.jsonl`, `results/resources-vm.jsonl` |
| Supervisor sessions and logs | `results/supervision/`; current pointer `results/supervision/latest.json` |
| Checkpoint payloads | `checkpoints/global_step_*/`; retention is two checkpoints |
| Original acceptance and reload evidence | `results/operator/checkpoint1-prefix-20260920T184935Z-3c9aeb9d.json`, `results/reload-proof/` |

Large results and checkpoints are referenced in place. The public config archive contains no credential files, raw transcripts, dataset rows or checkpoint payloads. R1 remains a separate root, `/data2/yuhanya/e2b-rl-pathfinding-20260920`, with [its own report](ATTEMPT-1.md).
