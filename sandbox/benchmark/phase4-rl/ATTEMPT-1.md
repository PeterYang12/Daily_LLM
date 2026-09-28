# R1: admission failure before the first training update

**R1 was stopped after a cancellation bug in our client admission path stalled its first training batch. It completed 0 native optimizer updates and saved no checkpoint.** This was an admission-accounting failure, not evidence of a hardware fault or an E2B capacity limit. The original runtime and evidence remain preserved.

Run: `ua-e2b-pathfinding-20260920`. The supervisor started at **2026-09-20 16:51:15 UTC**. Training used Qwen3-Coder-30B-A3B-Instruct, 62 SWE-rebench tasks, and a logical batch of **32 prompts × 8 rollouts**. Evaluation used a fixed 16-task SWE-bench Verified subset.

| Outcome | Recorded result |
|---|---|
| Native training updates | **0** |
| Trained checkpoint | **None** |
| Evaluation before training | **7/16 resolved (43.75%)** |
| Completed training episodes in the frozen stall snapshot | **85** |
| Completed training results in the final interrupted artifacts | **86** |

The baseline is a subset result, not a full 500-task SWE-bench Verified score. The 85/86 episode counts are not 256 completed rollouts, optimizer steps, or evidence of learning. The final count includes one additional result beyond the frozen inspection; other episodes lacked terminal results. [Native baseline metrics](/data2/yuhanya/e2b-rl-pathfinding-20260920/results/train-metrics.jsonl) · [Final supervisor status](/data2/yuhanya/e2b-rl-pathfinding-20260920/results/supervision/20260920T165115Z-4304e3db/status.json).

At the **18:09:04.921 UTC** pre-abort snapshot, **171 episodes were waiting for execution permits and no SDK command execution was active**. GPUs had been idle for more than 20 minutes. The last successful recorded SDK execution ended at 17:34:59 UTC and the last model roundtrip at 17:35:33 UTC. The stall therefore preceded the operator's shutdown. [Frozen pre-abort evidence and profile-prefix hashes](/data2/yuhanya/e2b-rl-pathfinding-20260920/results/server-diagnostics/exec-gate-stall/pre-abort-evidence.json).

The trace contained **57 cancelled exec-gate waits**, despite apparently balanced counters of **20,498 acknowledged acquisitions and 20,498 releases**. Cancelling a local await of a Ray actor's remote acquire did not reliably cancel that acquire inside the actor. A queued request could later take a permit after its caller had disappeared. The old client awaited acquisition outside its release `finally` block, so it could not reconcile that late grant. Balanced acknowledged-call counters did not include these lost grants.

An isolated real-Ray reproduction confirmed this mechanism: after a cancelled waiter received a late grant, a fresh caller could no longer acquire the single available permit. This reproduced our client/actor defect without E2B calls or GPU imports. The 57 cancelled waits identify the failure pattern; they are not a claim that 57 permits were simultaneously lost from a 16-permit pool. [Cancellation analysis, reproduction and repair](../phase5-optimization/GATE-CANCELLATION.md).

A separate startup problem caused **185 SDK-create cancellations before the abort**. The native 600-second startup deadline wrapped the shared create-gate wait as well as SDK creation and setup. One session waited **599.861 seconds** for admission, leaving only **0.139 seconds** before its SDK call was cancelled; its retry succeeded. These short caller-cancelled calls were not measured 600-second E2B API timeouts. Increasing the sandbox's 3,600-second lifetime would not address this queue-budget coupling. [Startup timing evidence](/data2/yuhanya/e2b-rl-pathfinding-20260920/results/server-diagnostics/rl-startup-and-filesystem-20260920/profile-evidence.json).

One episode also encountered a transient filesystem-write error. Captured client-proxy logs for its exact sandbox ID at **VM 17:18:47.490 UTC** show a **Redis catalog lookup deadline exceeded**, followed by “sandbox not found.” The proxy returned HTTP 502, which the SDK translated into `TimeoutException`. The same sandbox subsequently accepted the verifier script, executed verification, and was deleted normally after about 562 seconds. This was not observed lifetime expiry. The filtered capture contains four client-proxy lines and one nginx delete record; API and orchestrator filters selected no lines, so broader absence of server errors is not established. This isolated lookup failure does not explain the persistent admission deadlock. [Capture coverage](/data2/yuhanya/e2b-rl-pathfinding-20260920/results/server-diagnostics/rl-startup-and-filesystem-20260920/capture.json).

The operator sent SIGINT to the verified R1 driver at **18:09:04.922 UTC**, after freezing the stall evidence. Ray and owned-sandbox cleanup followed; the cleanup record reports **zero remaining owned sandboxes**. The supervisor ended at **18:09:26 UTC** with failure status. Cancellation and incomplete-span counts produced by this shutdown must be distinguished from the earlier 57 exec-wait cancellations and 185 startup cancellations. [Operator-abort record](/data2/yuhanya/e2b-rl-pathfinding-20260920/results/operator-abort.json) · [Cleanup result](/data2/yuhanya/e2b-rl-pathfinding-20260920/results/cleanup.json).

R2 started as a **fresh attempt at 18:49:35 UTC**, using cancellation-safe admission tickets and a **1,800-second startup budget**. Model, data, training objective, logical 32×8 batch, and create/exec gate limits of **3/16** remain unchanged. The repaired actor/provider passed **11 real-Ray regression cases** and **eight live sandbox smoke cases**; the smoke audit retired all 128 requested tickets and left no owned sandboxes. [Validation proof](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/admission-cancellation-validation/final-validation.json) · [Public test summary](../phase5-optimization/results/gate-cancellation-summary.json).

R2 has started; this record does **not** claim it has completed training or demonstrated a throughput improvement. Its optimizer, checkpoint, evaluation, and completion evidence must come from that new run. [Current RL status](STATUS.md).
