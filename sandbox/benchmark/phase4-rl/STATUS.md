# RL execution status — stopped by user

Verified 2026-09-21T02:48:12 UTC. **Stopped for analysis at the user's request. No training is running.**

Completed native trainer steps: **6/100**. Latest complete checkpoint: **6**. Step 7 was interrupted during rollout collection and is excluded from completed-step results.

The trainer and supervisor exited, the dedicated R2 container stopped, and all four sampler/publisher processes exited. Owned sandbox cleanup: **0 remaining, 0 errors**. Post-stop GPU sample: **0% activity on all 8 GPUs**, approximately **0.3 GiB VRAM used per GPU**.

The native `failed` / `KeyboardInterrupt` / exit-1 records describe this operator-requested interruption; they are preserved as recorded. They do not establish a new spontaneous failure. The last admission-state counters are stale after the actor exited.

Checkpoint 6 passed manifest and payload presence/size checks (40 payload files, 398.17 GiB). Full payloads remain for steps 1, 5 and 6. This stop did not reload checkpoint-6 tensors. The old `--resume-owned` supervisor path assumes checkpoint 1 and needs review before any future resume.

Current scope: **62 SWE-rebench training tasks and 16 SWE-bench Verified evaluation tasks**. Initial evaluation: **6/16 resolved**; later evaluation points were not reached. Six completed batches retained **1,534 trajectories**, with two failed training results. Partial step-7 results are retained separately.

[Completed-work inventory, six-step chart and configuration/sandbox analysis](../REVIEW-20260921.md).

Run: `ua-e2b-pathfinding-20260920-r2`. Private stop audit: `/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/stop-20260921/final-verification.json`.
