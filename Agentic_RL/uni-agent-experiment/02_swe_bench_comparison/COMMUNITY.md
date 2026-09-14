# Community context

This is a **2026-09-12 source snapshot review**, not a new benchmark or live survey. Only the harnesses and sandbox paths described in this repository were executed.

| Layer | Projects | Relevant comparison |
|---|---|---|
| Agent training orchestration | [Uni-Agent](https://github.com/verl-project/uni-agent), [Agent Lightning](https://github.com/microsoft/agent-lightning), [rLLM](https://github.com/rllm-org/rllm) | Harness reuse, task integration, trajectory/reward semantics, training loop |
| Training systems | [Miles](https://github.com/radixark/miles), [slime](https://github.com/THUDM/slime) | Rollout/training throughput, synchronization, checkpoints, AMD support |
| Coding harnesses / products | [Mini](https://github.com/SWE-agent/mini-swe-agent), [SWE-agent](https://github.com/SWE-agent/SWE-agent), [OpenHands](https://github.com/OpenHands/OpenHands) | Tool design, execution workflow, independent task success |
| Evaluation / execution | [Harbor](https://github.com/harbor-framework/harbor), [E2B](https://github.com/e2b-dev/E2B), [OpenSandbox](https://github.com/opensandbox-group/OpenSandbox) | Task format, lifecycle, image portability, execution isolation |

The next useful comparison needs a matched task and training protocol. Popularity or successful inference does not establish training quality. Independent SWE-agent was not run; mini-swe-agent is a different project.

[Saved community snapshot](results/community-snapshot-20260912.json)

