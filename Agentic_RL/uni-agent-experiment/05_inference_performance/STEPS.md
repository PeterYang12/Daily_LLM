# Replay: performance measurements

Use the complete lab. Check fixed GPU allocation in [ARCHITECTURE.md](../01_model_and_examples/ARCHITECTURE.md) and avoid competing clients on measured endpoints.

## 1. Start the required model pair

```bash
cd /home/yuhanya/uni-agent-lab
python3 scripts/labctl.py model --which tp1
python3 scripts/labctl.py model --which tp4
```

| Comparison | Launcher selections | Source script | Historical output |
|---|---|---|---|
| Tensor parallelism | `tp1`, `tp4` | [benchmark_serving.py](code/benchmark_serving.py) | `/lab/results/serving-benchmark` |
| Execution configuration | `tp1`, `aiter` | [benchmark_aiter.py](code/benchmark_aiter.py) | `/lab/results/serving-aiter-benchmark` |
| Replicas | `aiter`, `aiter-replica` | [benchmark_replicas_v2.py](code/benchmark_replicas_v2.py) | `/lab/results/serving-replicas-benchmark-v2` |

Start the services needed for the selected comparison. Check each host `/v1/models` endpoint for readiness.

## 2. Prepare a fresh output destination

Original scripts use fixed paths and refuse existing directories. Create a replay copy under `/lab/scripts` and change only its `ROOT=Path(...)` destination. Keep requests, endpoints, concurrency, and repetitions unchanged, and save the edited copy with the result.

## 3. Execute the replay copy

After creating `/lab/scripts/replay_benchmark_serving.py`:

```bash
docker exec ua-lab-cpu /lab/envs/cpu/bin/python \
  /lab/scripts/replay_benchmark_serving.py
```

Use equivalent replay copies for AITER and replicas.

## 4. Check completeness

Each comparison should have 18 batches: two configurations × three concurrency levels × three repeats. Each batch must return `concurrency × 256` output tokens. Derive means and ranges from the raw batches.

Saved raw/summary JSON pairs under [results/](results/) are the recorded experiment. Optimized-service API and single-task checks are with [experiment 1](../01_model_and_examples/README.md).

