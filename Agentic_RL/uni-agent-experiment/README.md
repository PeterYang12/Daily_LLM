# Uni-Agent on AMD ROCm

A Docker-based experiment with **Qwen3-Coder-30B-A3B-Instruct** on an **8 × AMD MI350X** machine. We tested agent inference, code repair, sandbox execution, and serving performance using fixed public weights. **No SFT or RL training was performed.**

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

## How to read this repository

Each experiment keeps its own `README.md` (what and results), `STEPS.md` (how to replay), `code/`, `figures/`, and `results/`. Configurations and short API notes are included where needed. Start with the experiment README; open raw results when checking evidence.

[Deployment diagram](01_model_and_examples/ARCHITECTURE.md) · [Uni-Agent API](01_model_and_examples/API.md) · [Docker sandbox API](03_docker_sandbox/API.md) · [E2B API](04_e2b_sandbox/API.md)

## Scope and runtime

- Claude Code is the real CLI using **local Qwen**, not an Anthropic model. ReAct runs in the CPU coordinator; Claude/Mini run inside task sandboxes.
- The SWE result covers a preselected 30-task sample, with 28 passing environment controls. It is not a full 500-task SWE-bench score.
- E2B (verdan in our discussion) was tested on a small repair task, not remote SWE-bench or remote Claude/Mini.
- Serving speedups measure HTTP inference throughput, not end-to-end agent speed or training gains.

Experiments ran on **2026-09-12**. This English report reorganizes their existing evidence. Full runtime assets remain in `/home/yuhanya/uni-agent-lab`; this repository contains source snapshots and lightweight results, not weights, installed environments, or Docker layers. Replay steps use that lab layout. To collect the distributed snapshots into `scripts/` and `configs/`, use the [code export instructions](06_trajectories_and_reproduction/STEPS.md).

Raw evidence and original experiment code are retained verbatim. [Artifact provenance and checksums](06_trajectories_and_reproduction/results/provenance/source-manifest.json)
