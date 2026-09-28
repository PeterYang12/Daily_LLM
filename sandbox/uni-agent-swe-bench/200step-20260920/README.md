# Manual 12K handoff

The old run and automatic controllers are stopped. The per-call generation limit is now12,288tokens; the128Kagent budget and remaining training configuration are retained. No new run has been started.

[Container shell recipe](/home/yuhanya/yuhan-uniAgent/experiments/rl-swe-e2b-200step-20260920/train_qwen3_moe_e2b_12k.sh) · [Manual launch instructions](RUN_12K.md)

The explicit recipe is ready and its container config check passes. Production training remains at **0 / 200 steps**; the previous attempt stopped during initial evaluation. Launching the recipe is left to the user.

The following records describe the earlier attempt and its infrastructure work.

---

# 200-step Uni-Agent experiment

Target:200 trainer steps, starting from the original Qwen3-Coder-30B-A3B-Instruct.

- Prompt batch64 × n8; mini-batch16 prompts;4 optimizer updates per trainer step.
- SWE-reBench1150 training tasks; SWE-bench Verified500 evaluation tasks.
- Evaluate before training and every10 steps; checkpoint every50, retain the latest complete checkpoint.
- 8 MI350X GPUs, colocate_async; actorTP1/PP1/CP4/EP8, vLLMTP4.

[Live local status](/home/yuhanya/uni-agent-lab/rl-swe-e2b-200step-20260920/runtime/results/STATUS.md) · [W&B infrastructure progress](https://wandb.ai/yuhanya-amd/uni-agent-swe-rl-rocm/runs/ua-qwen30b-e2b-200step-20260920-infra)

[Runtime artifacts](/home/yuhanya/uni-agent-lab/rl-swe-e2b-200step-20260920/runtime) · [Configuration decisions](/home/yuhanya/uni-agent-lab/rl-swe-e2b-200step-20260920/runtime/results/config-decisions.json)

Preflight passed: 512 simultaneous sandboxes with cleanup; eight-GPU arithmetic and collectives; Ray GPU mapping; adapted Megatron mesh; baseline/reference grading of a train and an evaluation task; model inference smoke; actual Megatron forward/backward and full checkpoint save/restore. These checks are separate from production training and the full benchmark.

The original-model baseline is running. Its first attempt encountered E2B capacity failures, so a recovery process preserves the first completed repair result for each task and reruns only tasks without an application result. Full accuracy is published only after all 500 tasks have results. Production training waits for that validated baseline and qualified startup paths for all task environments.

All 1,650 public image manifests resolved and are pinned by digest. All 500 evaluation environments are ready. Most training images had missing layers in Google's public cache. After 1,059 templates were ready, E2B snapshot persistence failed and then sandbox creation failed temporarily. Creation recovered; 591 remaining training environments now use qualified direct OCI preparation at startup, while all500evaluation environments remain cached. A reconstructed environment passed file/exec consistency, persistent-shell checks, and original-fail/reference-pass grading. Existing Docker Hub credentials were never sent to E2B; the earlier credential-authorization question is no longer needed.

E2B capacity limits and snapshot timeouts are recorded separately. Bulk snapshot construction is paused after a backend persistence failure. Direct preparation at startup is limited to16concurrent preparations and does not require new snapshot writes. Run-owned active sandboxes receive lease renewal so a slow rollout can use its configured 128K/200-turn budget. The live status file records current counts and process liveness; this page describes the run policy.

The controller waits for qualified environment availability across the full dataset and preflight checks before starting production. Sandbox exceptions are recorded separately; unparseable grading results are flagged for diagnosis. The full task sets are preserved.

The default 600-second verifier limit is preserved. One initial Requests task and its isolated reference-patch check both reached that limit while waiting on network tests. Incomplete verifiers retain the native unresolved score and are counted separately in the final report; this is a limitation of interpreting those scores as model repair quality.

[Backend incident and recovery](E2B-INCIDENT.md). Full dataset availability:1,059cached templates plus591environments prepared at startup. The direct path passed original/reference grading and file/shell checks.
