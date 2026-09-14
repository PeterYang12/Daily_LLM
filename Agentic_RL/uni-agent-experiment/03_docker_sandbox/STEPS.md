# Replay: local sandbox

Use the complete lab and running services from [experiment 1](../01_model_and_examples/STEPS.md). The image `ua-lab/demo:20260912` includes tmux and numpy.

## 1. Run the official tool demo

```bash
docker exec -e DEBUG_MODE=1 -e SANDBOX_PROVIDER=docker \
  -e IMAGE=ua-lab/demo:20260912 ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/src/uni-agent/examples/quickstart/sandbox/demo.py
```

Check shell/editor workflow, directory persistence, and file round trips against the [recorded log](results/docker-official-demo.txt).

## 2. Run isolation and lifecycle checks

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/sandbox_checks.py \
  --output /lab/results/replay-sandbox-checks
```

Use a fresh output name. Verify container/file/resource state, not only command exit. TTL is handled separately by [sandbox_janitor.py](code/sandbox_janitor.py); its controls are in [janitor-validation.json](results/janitor-validation.json).

## 3. Repeat the paired repair experiment

```bash
cd /home/yuhanya/uni-agent-lab
python3 scripts/labctl.py model --which tp1

docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/run_sandbox_agent.py \
  --output /lab/results/replay-sandbox-agent
```

The preserved runner executes **both Docker and E2B**, reads `/lab/secrets/e2b.env`, and needs [E2B prerequisites](../04_e2b_sandbox/STEPS.md). It has no Docker-only flag. The demo and isolation check above work without E2B.

Inspect `docker/` and `e2b/` under the new result directory. Each records baseline, agent output, final code, and verifier result. Different sampled wall times are not a controlled sandbox speed comparison.

The runner imports the [Docker adapter](../02_swe_bench_comparison/code/lab_runtime.py) and [E2B adapter](../04_e2b_sandbox/code/e2b_provider.py). They reside together under `/lab/scripts` at runtime; the [code exporter](../06_trajectories_and_reproduction/STEPS.md) reconstructs that layout.

