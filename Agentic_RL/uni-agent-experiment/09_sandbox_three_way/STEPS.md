# Replay the three-deployment comparison

The prepared lab requires the existing local E2B service, remote E2B credentials, the pinned SWE templates, and model weights. Secrets stay in `/home/yuhanya/uni-agent-lab/secrets/`.

## 1. Prepare a fresh run

```bash
cd /home/yuhanya/uni-agent-lab
THREEWAY_NAME="sandbox-three-way-$(date -u +%Y%m%dT%H%M%SZ)"
python3 scripts/sandbox_three_way/prepare.py --root "results/$THREEWAY_NAME"
```

This creates a new manifest and reuses the pinned templates. It starts or reuses `ua-threeway-model`; review GPU availability first. The model endpoint is `http://127.0.0.1:18087/v1` on the host.

## 2. Collect telemetry and run the pipeline

In a separate host terminal, with the same THREEWAY_NAME:

```bash
python3 scripts/swe_sandbox_profile/monitor.py   --root "results/$THREEWAY_NAME" --metrics-url http://127.0.0.1:18087/metrics   --output-name telemetry.jsonl --stop-file STOP_MONITOR
```

Run the experiment from the host:

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python   /lab/scripts/sandbox_three_way/pipeline.py --root "/lab/results/$THREEWAY_NAME"

docker exec ua-lab-cpu /lab/envs/cpu/bin/python   /lab/scripts/sandbox_three_way/login_probe.py --root "/lab/results/$THREEWAY_NAME"
```

The pipeline runs 300 fixed-operation samples, two persistent-shell probes, six controls, and nine serial agent rollouts. Provider order rotates between the three repetitions. The login probe runs afterward and leaves the sandbox image and profile files unchanged.

## 3. Analyze and clean up

```bash
python3 scripts/sandbox_three_way/analyze.py --root "results/$THREEWAY_NAME"

docker exec ua-lab-cpu /lab/envs/cpu/bin/python   /lab/scripts/sandbox_three_way/cleanup_and_audit.py --root "/lab/results/$THREEWAY_NAME"

touch "results/$THREEWAY_NAME/STOP_MONITOR"
docker stop ua-threeway-model
```

The analysis includes each rollout, component times, generated tokens, and tool-call counts. `figures.py` renders the figures with Matplotlib in the existing report environment. Keep the local E2B VM/API running for subsequent tasks.

`worker.py` reuses the existing profiled task and provider implementations. It changes credentials by deployment and sets a common 3,600-second sandbox lifetime. The original code at run start is preserved under `code/executed/`; current reporting helpers are under `code/`.
