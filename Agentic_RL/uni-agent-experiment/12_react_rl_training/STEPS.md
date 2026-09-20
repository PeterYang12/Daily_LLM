# Operate the RL run

These commands use the prepared lab. Training is paused by the user. Inspect the existing artifacts; do not restart training or GPU diagnostics without a new instruction.

## Progress and artifacts

```bash
python3 /home/yuhanya/uni-agent-lab/rl-swe-e2b-20260916/code/status.py
```

- Training container: `ua-rl-gpu`; interpreter: `/opt/venv/bin/python`.
- Runtime root in the container: `/lab/rl-swe-e2b-20260916`.
- Main log: `logs/main-training.log`.
- Metrics: `results/main-metrics.jsonl` and `results/main/metrics-timestamped.jsonl`.
- Checkpoints: `checkpoints/global_step_<N>`; step 100 is retained for recovery.
- Frozen dataset IDs and hashes: `data/frozen-manifest.json`.
- Task evidence: `results/sessions/<session_id>`.
- Token trajectories: `results/trajectories`, stored as JSON metadata and NPZ arrays.
- W&B: [live run](https://wandb.ai/yuhanya-amd/uni-agent-swe-rl-rocm/runs/ua-qwen30b-e2b-20260916).

The existing E2B and W&B credentials are stored separately under `/home/yuhanya/uni-agent-lab/secrets`. The report never includes their values.

## Training entrypoint

The recorded main launch is a detached Docker execution of `code/main_job.sh`, which runs `code/train.sh` with [train.yaml](configs/train.yaml) and [tasks-main.yaml](configs/tasks-main.yaml). The [resolved configuration](configs/final-resolved.yaml) records the effective trainer settings.

`main_job.sh` writes `results/main-exit.json` when the training process exits. A successful exit alone is not the acceptance criterion: the final audit also checks all 300 steps, the evaluation points, trajectories, checkpoint contents, and cleanup.

The E2B provider is a local extension in `code/ua_rl/provider.py`. Its direct process RPC adapter is pinned to E2B SDK 2.49.1. The agent and verifier each initialize the sandbox's login environment once, then execute commands through argv RPC. The ReAct stateful shell remains stateful across calls.

## Reproduction order

The final report will include the complete replay recipe. The existing preparation and training code already records these stages:

1. Install the pinned Uni-Agent/verl source and lightweight task dependencies into the supplied ROCm Docker image.
2. Normalize the recommended SWE-reBench corpus and select separate SWE-bench Verified evaluation candidates.
3. Build pinned E2B templates and qualify each task using baseline and official-gold controls.
4. Freeze the 62/16 dataset and verify its hashes.
5. Verify real gradient/parameter updates in the two-step integration pilot.
6. Validate direct E2B execution and launch the 300-step run with W&B.
7. Audit the completed run, verify the final saved model, and publish the full performance analysis.

Do not launch another copy of `main_job.sh` against the active run's GPU resources or output directory. A separate replay needs a distinct run directory and W&B run ID.

## Recovery after the first attempt

The continuation uses `code/recovery_job.sh` under a host supervisor (`code/supervise_recovery.py`). It resumes checkpoint 100, appends retained metrics, uses a separate W&B run, and verifies sampled restored model values and scheduler state on all ranks. The supervisor checks GPU arithmetic before each bounded retry and preserves failed attempts. It only restarts the dedicated training container.

On this ROCm 7.14 image, the first lazy RCCL reduction produced wrong sums with `PYTORCH_ALLOC_CONF=expandable_segments:True`. Disabling expandable segments passed BF16/FP32 reduction, reduce-scatter, gather, all-to-all and TP2 group checks on all eight GPUs. The continuation explicitly sets `expandable_segments:False`. No host driver or kernel change was made.

The checkpoint does not contain a TransferQueue snapshot. In-flight rollouts are lost on recovery; model/optimizer/dataloader recovery does not imply exact replay of that work.
