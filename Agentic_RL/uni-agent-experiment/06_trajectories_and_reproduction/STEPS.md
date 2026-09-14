# Replay and reproduction

## 1. Validate this report without running a model

From the repository root:

```bash
python3 06_trajectories_and_reproduction/code/reporting/validate_bundle.py
```

This checks totals, preserved hashes, links, English Markdown, and directory organization. It does not call model or sandbox services.

## 2. Export the experiment code

```bash
python3 06_trajectories_and_reproduction/code/export_code.py \
  --output /tmp/uni-agent-replay-code
```

Use a destination that does not exist. The exporter collects the six folders into the original `scripts/`, `configs/`, and `patches/` layout and verifies hashes. This resolves cross-experiment imports. It exports code only, without installing dependencies or starting containers.

Original main-run versions remain under `scripts/executed/`. The ordinary `scripts/run_swe.py` is the later version with explicit logprob capture.

## 3. Repeat an explicit-logprob session

After [model/task setup](../02_swe_bench_comparison/STEPS.md), run the later driver:

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python /lab/scripts/run_swe.py \
  --data /lab/results/data/validated-twenty-eight.parquet \
  --mode react --config /lab/configs/swe-react-128k.yaml \
  --output /lab/results/replay-logprobs-react --gateway --limit 1 \
  --base-url http://172.30.90.5:8000/v1 \
  --context-length 131072 --max-tokens 4096 \
  --agent-timeout 1800 --concurrency 1
```

Repeat with Claude/Mini mode and config, using separate outputs. Check token IDs, binary masks, matching token/mask/logprob lengths, and finite logprobs. [audit_trajectories.py](code/audit_trajectories.py) contains the implementation; change its fixed report destination in a replay copy before rerunning.

## 4. Reconstruct a complete runtime

The original lab retains this lightweight kit:

```text
/home/yuhanya/uni-agent-lab/deliverables/uni-agent-rocm-reproduction-kit-20260912.tar.gz
SHA256: 5851f6fae73178e49f5eddaad66137f9232052271cd1c12281c368dd29e7f200
```

Verify the archive and unpack into an empty directory with GNU tar, preserving links. Follow its runtime instructions:

1. Create CPU and dedicated daemon services with `labctl.py up`.
2. Run `/lab/scripts/bootstrap_locked.sh` in the CPU container.
3. Download the pinned model; restore task images by digest.
4. Prepare tmux, fixed Claude CLI, and portable Mini runtime.
5. Validate APIs, sandboxes, and baseline/gold task controls.
6. Run agents, regrade candidates, inspect trajectories.

The kit includes source/config/data snapshots, not weights, Docker layers, environments, CLI binaries, or credentials. Review fixed container names, GPU IDs, subnet, and host bind paths when changing lab roots.

## 5. Fixed inputs and report maintenance

Image digests and model hashes are in [experiment 1](../01_model_and_examples/results/environment/); dependency locks are in its [configs/](../01_model_and_examples/configs/). The CPU rebuild validates selected inference paths, not all verl training dependencies.

Regenerate English figures with [generate_figures.py](code/reporting/generate_figures.py) and [render_diagrams.py](code/reporting/render_diagrams.py). They read saved data and local Mermaid assets only.

