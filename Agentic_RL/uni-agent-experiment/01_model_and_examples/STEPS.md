# Replay: model and official examples

These commands use the existing complete lab at `/home/yuhanya/uni-agent-lab`. Inference and agents run inside Docker. Files in `code/` are source snapshots; launchers expect the original lab layout.

## 1. Check and start services

```bash
cd /home/yuhanya/uni-agent-lab
python3 scripts/labctl.py status
python3 scripts/labctl.py up
python3 scripts/labctl.py model --which tp1
curl --fail http://127.0.0.1:18082/v1/models
```

`up` starts the CPU coordinator, dedicated sandbox daemon, and janitor. `tp1` selects GPU 0 and the 64K eager service. Check GPU availability before starting the fixed configuration.

For a new machine, follow [reconstruction](../06_trajectories_and_reproduction/STEPS.md) first. Dependencies, weights, task images, Claude CLI, and the Mini runtime must already be prepared.

## 2. Run the official ReAct example

Choose a new output name for each replay.

```bash
docker exec -e NUM_WORKERS=1 ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/src/uni-agent/examples/inference/parallel_infer_api.py \
  --data-path /lab/results/data/pilot-six.parquet \
  --task-config /lab/configs/swe-react.yaml \
  --base-url http://172.30.90.3:8000/v1 \
  --model Qwen3-Coder-30B-A3B-Instruct --api-key EMPTY \
  --concurrency 1 --limit 1 \
  --log-dir /lab/results/replay-official-react/logs \
  --result-path /lab/results/replay-official-react/summary.json
```

For Claude Code, change the config to `/lab/configs/swe-claude.yaml` and use `replay-official-claude` for both output paths. Both use Qwen. The recorded runs each resolved the Flask task; replay can produce a different sampled patch.

## 3. Inspect and finish

Read the new summary and logs; require a verifier pass, not just process exit. [Recorded results](README.md) are the reference.

```bash
python3 scripts/labctl.py status
python3 scripts/labctl.py stop-models
python3 scripts/labctl.py stop
```

These stop named lab containers without deleting model files or results. Start the 128K model separately for [SWE-bench](../02_swe_bench_comparison/STEPS.md).

