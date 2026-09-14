# 1. Model setup and official examples

**Result:** the local 30B model served requests on ROCm, and Uni-Agent's official ReAct and Claude Code entry points both repaired a real repository task.

## What ran

| Check | Result |
|---|---|
| GPU preflight | All eight GPUs detected; a small matrix multiplication passed on each. |
| Model API checks | Chat, structured tool calls, Anthropic-compatible messages, and token-ID/logprob output passed. |
| Official ReAct + eager vLLM | `pallets__flask-5014`: 1/1 resolved. |
| Official Claude Code + eager vLLM | Same task: 1/1 resolved. |
| Optimized serving checks | API checks, official ReAct task, and a separate ReAct + Gateway task passed. |

The task required rejecting an empty Flask Blueprint name. Agents read the repository, changed code, and passed the verifier. Official API examples called vLLM directly; the additional Gateway run verified the trajectory path.

## Fixed environment

| Component | Version / setting |
|---|---|
| Hardware | 8 × MI350X; approximately 252 GiB visible VRAM per GPU |
| Model | Qwen/Qwen3-Coder-30B-A3B-Instruct, BF16; approximately 57 GiB of weights |
| Model revision | `b2cff646eb4bb1d68355c01b18ae02e7cf42d120` |
| Uni-Agent commit | `10743439dd0a19da44a94cccad069b135d957bf1` |
| Bundled verl commit | `a9f2985159536a607211dcac730d3f5d55028950` |
| Runtime | Python 3.12.13; Torch `2.12.0+git6bbd260`; HIP `7.2.53211` |
| vLLM | `0.28.1rc1.dev337+g27a94d1ce.rocm723` |
| Agent harnesses | Claude Code 2.1.236; mini-swe-agent 2.2.8 |

The model fit on one GPU. Detecting eight GPUs does not mean the main task used eight-way tensor parallelism. The later logging patch is preserved in [experiment 6](../06_trajectories_and_reproduction/README.md).

## Contents

- [STEPS.md](STEPS.md): start the lab and repeat the official example.
- [ARCHITECTURE.md](ARCHITECTURE.md): containers, processes, and model endpoints.
- [API.md](API.md): Uni-Agent interfaces needed here.
- [code/](code/): launchers, bootstrap, model download, and runtime preparation.
- [configs/](configs/): dependency locks and official-example configurations.
- Evidence: [GPU preflight](results/environment/gpu-preflight.txt), [runtime](results/environment/runtime.json), [image digests](results/environment/image-digests.txt), [ReAct](results/official-api-react.json), [Claude](results/official-api-claude.json), [optimized ReAct](results/official-api-react-aiter-fixed.json).

[Back to overview](../README.md)

