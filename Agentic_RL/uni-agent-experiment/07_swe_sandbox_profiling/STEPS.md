# Replay the sandbox profiling study

These steps use the prepared lab at `/home/yuhanya/uni-agent-lab`. Model weights, Uni-Agent/verl, Python environments, Docker images, and E2B credentials are runtime prerequisites. The report contains code and lightweight evidence; [experiment 6](../06_trajectories_and_reproduction/STEPS.md) covers reconstruction.

## 1. Start the pinned model services

```bash
cd /home/yuhanya/uni-agent-lab
python3 scripts/swe_sandbox_profile/start_models.py
curl --fail http://127.0.0.1:18083/v1/models
curl --fail http://127.0.0.1:18087/v1/models
```

Wait for both endpoints. They share the fixed BF16 weights, 128K context limit, TP1, 16 active sequences, and an 8192-token batch cap. The optimized service additionally enables AITER and default compilation/graphs. E2B settings remain in `secrets/e2b.env`.

## 2. Create a new result directory

```bash
PROFILE_NAME="swe-profile-$(date -u +%Y%m%dT%H%M%SZ)"
PROFILE_DOCS=/home/yuhanya/Daily_LLM/Agentic_RL/uni-agent-experiment/07_swe_sandbox_profiling
mkdir -p "results/$PROFILE_NAME/assets"
cp "$PROFILE_DOCS/configs/manifest.json" "results/$PROFILE_NAME/manifest.json"
cp "$PROFILE_DOCS/assets/tmux.tar.gz" "results/$PROFILE_NAME/assets/tmux.tar.gz"
```

The manifest fixes the 12 issues and image digests. Ensure those digests are available to the dedicated Docker daemon. For a new runtime, pull the manifest images through the Docker CLI inside `ua-lab-cpu`. The portable tmux archive includes dereferenced runtime libraries; its checksum is in [assets/tmux-provenance.json](assets/tmux-provenance.json).

## 3. Build E2B templates and validate environments

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/swe_sandbox_profile/prepare_templates.py \
  --root "/lab/results/$PROFILE_NAME"

docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/swe_sandbox_profile/run.py \
  --root "/lab/results/$PROFILE_NAME" --mode controls --tag controls --concurrency 2
```

Qualification requires a completed failing baseline, a passing official patch, and matching initial repository trees on both providers. Runtime preparation restores standard localhost resolution and checks tmux before agent execution. It does not alter benchmark source or tests.

The driver reads `configs/swe-react-128k.yaml` for ReAct and prompt settings, then replaces the sandbox block. Local 4-CPU/8-GiB limits are in `providers.py`; E2B 4-CPU/8192-MiB limits are in `prepare_templates.py`. The old mounts and 2-CPU setting in the base YAML are not active. Each result directory saves the effective configuration as `input-config.json`.

The original study's accepted controls are indexed in `controls-final-index.json`; its Matplotlib controls were repeated after localhost preparation was corrected. A fresh replay with the final code can use a single `controls` tag as above.

## 4. Start telemetry and run the primary pairs

In a separate host terminal, run the optimized-service monitor with the same PROFILE_NAME value:

```bash
python3 scripts/swe_sandbox_profile/monitor.py \
  --root "results/$PROFILE_NAME" --metrics-url http://127.0.0.1:18087/metrics \
  --output-name telemetry-optimized.jsonl --stop-file STOP_MONITOR_OPT
```

Then run the primary tasks:

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/swe_sandbox_profile/run.py \
  --root "/lab/results/$PROFILE_NAME" --mode react --tag main-optimized \
  --controls-tag controls --concurrency 1 --base-url http://172.30.90.9:8000/v1
```

Each qualified task runs once per provider. Provider order alternates. The driver creates a fresh verifier environment, filters recognized test-path modifications from the candidate, runs the upstream SWE verifier, and exports token/mask/logprob records with the task reward. It performs no training update.

## 5. Run follow-up diagnostics after the primary batch

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/swe_sandbox_profile/followup_pipeline.py \
  --root "/lab/results/$PROFILE_NAME" --controls-tag controls

docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/swe_sandbox_profile/microbench.py --root "/lab/results/$PROFILE_NAME"

docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/swe_sandbox_profile/native_shell_probe.py --root "/lab/results/$PROFILE_NAME"

docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/swe_sandbox_profile/verifier_cpu_probe.py --root "/lab/results/$PROFILE_NAME"
```

The pipeline runs the 1-vs-4-task scheduling study, then the fixed-context serving and logprob studies. Keep other clients off these model endpoints during measurement. The native-shell probe is an experimental transport check; it is not the adapter used for primary task scores.

## 6. Analyze and inspect

```bash
python3 scripts/swe_sandbox_profile/analyze.py --root "results/$PROFILE_NAME"
```

Generate figures with `figures.py` in the existing report Python environment (Matplotlib is required). Results are under `analysis/`: case CSV/JSON, tool-call samples, scheduling statistics, and a Chrome-trace-compatible timeline. `PROFILE.md` explains metric boundaries and nested timing categories.

```bash
docker run --rm --network none --cpus 2 --memory 4g \
  --user "$(id -u):$(id -g)" -e MPLCONFIGDIR=/tmp/mpl-profile \
  -v /home/yuhanya/uni-agent-lab:/lab:ro \
  -v "/home/yuhanya/uni-agent-lab/results/$PROFILE_NAME:/profile" \
  --entrypoint /lab/envs/report/bin/python \
  vllm/vllm-openai-rocm@sha256:91e381f072d6a44e1e4c97c82dce06e50e5189905cb3999a11471c5a8fc6a563 \
  /lab/scripts/swe_sandbox_profile/figures.py --root /profile
```

## 7. Audit and release resources

After all workers and diagnostics finish:

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/swe_sandbox_profile/cleanup.py --root "/lab/results/$PROFILE_NAME" --delete

touch "results/$PROFILE_NAME/STOP_MONITOR_OPT"
docker stop ua-profile-model-aiter-128k ua-lab-model-128k
```

Cleanup targets only E2B sandbox IDs recorded by this run or instances with its exact experiment label, and local containers carrying the run's `ua-swe-profile` label. Templates are retained for replay. The existing CPU coordinator and dedicated Docker daemon remain available.
