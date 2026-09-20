# Uni-Agent on AMD ROCm

A Docker-based study with **Qwen3-Coder-30B-A3B-Instruct** on an **8 × AMD MI350X** machine. Experiments 1–11 tested agent inference, code repair, sandbox execution, and serving performance using fixed public weights. **Experiment 12 ran full RL training and is paused after a GPU hardware fault** with ReAct and a user-hosted E2B service.

**Experiment 13 attempted a three-step colocated ReAct RL run with 128 concurrent E2B sessions.** One update completed and 256 rollout trajectories were validated before an uncorrectable GPU event interrupted step 2. No new checkpoint was saved.

**For management:** [Uni-Agent introduction](introduction.md) · [One-page PDF](introduction.pdf)

## Experiments

| # | Experiment | Main result |
|---|---|---|
| 1 | [Model setup and official examples](01_model_and_examples/README.md) | 30B runs on ROCm; official ReAct and Claude Code examples passed a real Flask task. |
| 2 | [SWE-bench agent comparison](02_swe_bench_comparison/README.md) | Same 28 qualified tasks: **ReAct 17**, **Claude Code 11**, **Mini 12** resolved. |
| 3 | [Local Docker sandbox](03_docker_sandbox/README.md) | Execution, files, isolation, resource limits, and cleanup verified; small repair task passed **10/10** tests. |
| 4 | [Remote E2B sandbox](04_e2b_sandbox/README.md) | Lifecycle, official demo, and ReAct repair verified; the same small task passed **10/10** tests. |
| 5 | [Inference performance](05_inference_performance/README.md) | At measured concurrency: AITER + graphs gave **4.41×** throughput; two replicas gave **1.80×**. |
| 6 | [Trajectories and reproduction](06_trajectories_and_reproduction/README.md) | 84 main sessions passed token/mask checks; separate logprob checks and a clean CPU rebuild completed. |
| 7 | [ReAct SWE-bench: Docker vs E2B](07_swe_sandbox_profiling/README.md) | **9/12 resolved on each provider**; matched task images, fresh verifiers, valid trajectories, and component-level rollout profiles. |
| 8 | [Self-hosted E2B on Kubernetes](08_local_e2b/README.md) | Local E2B API in a dedicated VM; SDK lifecycle, files, persistent shell, four concurrent sandboxes, restart recovery, and a full Uni-Agent Flask repair passed. |
| 9 | [Same-task sandbox comparison](09_sandbox_three_way/README.md) | Three Flask rollouts per deployment with one model service; Docker, local E2B, and remote E2B tool and task timings compared. |
| 10 | [OpenSandbox on Kubernetes](10_local_opensandbox/README.md) | New native-session Uni-Agent provider; full Flask repair, output/file/cancellation checks, and preservation of sandbox state across API restart passed. |
| 11 | [Larger local E2B](11_local_e2b_capacity/README.md) | New 96-vCPU / 512-GiB VM; existing API key and templates retained; 24 concurrent SWE environments verified; full VM restart passed. |
| 12 | [Full ReAct RL with E2B](12_react_rl_training/README.md) | Paused: latest optimizer step 112/300, checkpoint 100 retained; real E2B rollouts, full parameter updates, W&B metrics and sandbox profiling. |
| 13 | [Three-step colocated ReAct RL](13_colocate_e2b_3step/README.md) | Interrupted at step 2: **1/3 updates completed**, 256 valid trajectories, 128-sandbox acceptance passed; uncorrectable ATHUB event and automatic GPU reset; no checkpoint. |

## How to read this repository

Each experiment keeps its own `README.md` (what and results), `STEPS.md` (how to replay), `code/`, `figures/`, and `results/`. Configurations and short API notes are included where needed. Start with the experiment README; open raw results when checking evidence.

[Deployment diagram](01_model_and_examples/ARCHITECTURE.md) · [Uni-Agent API](01_model_and_examples/API.md) · [Docker sandbox API](03_docker_sandbox/API.md) · [E2B API](04_e2b_sandbox/API.md)

## Scope and runtime

- Claude Code is the real CLI using **local Qwen**, not an Anthropic model. ReAct runs in the CPU coordinator; Claude/Mini run inside task sandboxes.
- The SWE result covers a preselected 30-task sample, with 28 passing environment controls. It is not a full 500-task SWE-bench score.
- The initial E2B experiment used a small repair task. Experiment 7 adds real remote SWE-bench with ReAct and Gateway; remote Claude/Mini remain outside the measured scope.
- Serving speedups measure HTTP inference throughput, not end-to-end agent speed or training gains.

The current local E2B service is experiment 11's **96-vCPU / 512-GiB VM**. Experiment 8's small VM is stopped, with its disk and historical results retained. OpenSandbox remains available separately. Experiment 11 validates sandbox capacity and SWE verification; it does not add a model rollout or RL training run.

Experiment 13 used the separate user-hosted E2B endpoint at `10.145.64.67:8088`, reported as 192 CPUs / 1 TB RAM with quota 512. Its raw runtime assets are under `/home/yuhanya/yuhan-uniAgent/experiments/colocate-e2b-3step-20260920`; its report records the actual measured capacity, completed work and GPU failure.

Experiments 1–6 ran on **2026-09-12**, experiment 7 on **2026-09-14**, and experiments 8–11 on **2026-09-16**. Full runtime assets remain in `/home/yuhanya/uni-agent-lab`; this repository contains source snapshots and lightweight results, not weights, installed environments, VM disks, or Docker layers. Replay steps use that lab layout. To collect the distributed snapshots into their runtime paths, use the [code export instructions](06_trajectories_and_reproduction/STEPS.md).

Raw evidence and original experiment code are retained verbatim. [Artifact provenance and checksums](06_trajectories_and_reproduction/results/provenance/source-manifest.json)
