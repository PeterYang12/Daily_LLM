# Replay: SWE-bench comparison

Prerequisite: [model setup](../01_model_and_examples/STEPS.md), task images, and prepared data in the complete lab. Dataset: `princeton-nlp/SWE-bench_Verified`, revision `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`.

## 1. Validate the task environments

Run each control into a new directory:

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/run_swe.py \
  --data /lab/results/data/stratified-thirty.parquet --mode baseline \
  --output /lab/results/replay-baseline --concurrency 4

docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/run_swe.py \
  --data /lab/results/data/stratified-thirty.parquet --mode oracle \
  --output /lab/results/replay-oracle --concurrency 4
```

A qualified task requires completed baseline/oracle evaluation, baseline unresolved, and oracle resolved. The recorded run qualified 28 tasks; `psf__requests-2317` and `pydata__xarray-3993` did not qualify. Recompute the gate on a new machine.

Preparation: [prepare_data.py](code/prepare_data.py), [prepare_images.py](code/prepare_images.py). The frozen manifests are in [results/](results/).

## 2. Generate one candidate per task

```bash
cd /home/yuhanya/uni-agent-lab
python3 scripts/labctl.py model --which 128k
curl --fail http://127.0.0.1:18083/v1/models

docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/run_swe.py \
  --data /lab/results/data/validated-twenty-eight.parquet \
  --mode react --config /lab/configs/swe-react-128k.yaml \
  --output /lab/results/replay-react --gateway \
  --base-url http://172.30.90.5:8000/v1 \
  --context-length 131072 --max-tokens 4096 \
  --agent-timeout 1800 --concurrency 3
```

Repeat with `--mode claude` / `swe-claude-128k.yaml` / `replay-claude`, and `--mode mini` / `swe-mini-128k.yaml` / `replay-mini`. Keep other comparison settings consistent.

The later replay driver explicitly enables session logprobs. The original driver in [code/executed/](code/executed/) did not; historical results are preserved.

## 3. Regrade saved candidates

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/regrade_swe.py \
  --input /lab/results/replay-react --output /lab/results/replay-react-graded \
  --data /lab/results/data/validated-twenty-eight.parquet --concurrency 3
```

Use each harness's input and a separate output. The regrader reconstructs the agent delta relative to the initial image tree, applies it in a fresh sandbox, and runs the verifier. It does not call the model.

## 4. Inspect the result

Require the expected task IDs and inspect `resolved`, `finished`, `eval_completed`, and existing-test modifications separately. [RESULTS.md](RESULTS.md) and the [CSV](results/per-case-results.csv) record the completed experiment. Fixed versions do not guarantee identical sampled patches.

