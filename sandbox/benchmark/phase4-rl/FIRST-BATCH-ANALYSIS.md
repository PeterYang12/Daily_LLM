# R2 first batch: why the final runners finished late

**The final tail was dominated by model roundtrips, after substantial earlier admission queueing.** Startup accounted for 58.8% of the final twelve runners' complete lifetimes; 95.0% of that startup time was waiting for the create gate. Once completion 244 had returned, startup was already over for all twelve remaining runners. In that final window, model roundtrips accounted for 85.9% of their cumulative remaining time. These are observed timing boundaries, not a causal speedup estimate or a measurement of pure GPU computation.

All **256 runner results** in R2's first training batch completed, covering 32 prompts × 8 rollouts in one supervisor attempt. Their collection interval was **3017.707 seconds (50.30 minutes)**. The nearest-rank 95% milestone is completion **244**, at **19:51:39.469 UTC**; the last runner returned at **19:59:05.237 UTC**. The interval between them was **445.768 seconds (7.43 minutes)**, or 14.77% of collection wall time. It contains the last **12/256 runners (4.6875%)**, rather than exactly 5%.

The exact twelve session IDs, task IDs, per-session values, trace references and validation results are available in the [accounting CSV](results/first-batch-r2/last12-accounting.csv) and [accounting JSON](results/first-batch-r2/last12-accounting.json). Selection and resource coverage come from the existing [first-batch manifest](results/first-batch-r2/first-batch.json) and [completion/resource timeline](results/first-batch-r2/first-batch-timeline.png).

The two views below answer different questions. **Both columns sum concurrent session-seconds across those same twelve runners; neither is elapsed batch wall time.** The full-lifetime column explains their accumulated delay. The clipped column measures only what they were doing after completion 244.

| Non-overlapping category | Twelve complete lifetimes: session-seconds | Final 445.768-second window: session-seconds |
|---|---:|---:|
| Startup through first model query | 18,824.755 | 0.000 |
| Model roundtrips | 8,511.905 | 1,429.081 |
| Logical tools after startup | 4,521.542 | 81.766 |
| Verifier | 143.595 | 143.595 |
| Toolbox/sandbox cleanup | 9.772 | 9.749 |
| Unattributed outside these categories | 1.143 | 0.199 |
| **Total concurrent session-seconds** | **32,012.712** | **1,664.391** |

The earlier startup queue contributed **17,891.264 session-seconds**. Logical tools also accumulated **3,259.516 seconds waiting for exec permits** over the complete lifetimes. Those waits are already inside the table's parent categories and must not be added again. During the final window, exec-gate waiting inside logical tools fell to only **0.452 seconds**; SDK command-call wall time accounted for **78.909 seconds** of the 81.766 tool seconds. The final tail was therefore not primarily another startup/exec-gate queue or a long verifier run, even though earlier queueing materially delayed when these runners could begin model work.

The final three completions were all `agronholm__anyio-639`:

| Completion rank / rollout | Complete runner lifetime | Startup | Model roundtrips | Logical tools | Verifier | Model time inside the final window |
|---|---:|---:|---:|---:|---:|---:|
| 254 / rollout 7 | 2,831.035 s | 1,438.776 s | 886.130 s | 481.614 s | 23.889 s | 263.100 of 303.199 s |
| 255 / rollout 0 | 2,862.622 s | 1,394.730 s | 1,002.190 s | 448.028 s | 17.073 s | 305.556 of 333.853 s |
| 256 / rollout 4 | 2,974.367 s | 1,413.071 s | 1,107.692 s | 435.642 s | 17.258 s | 406.075 of 445.768 s |

Their exact session IDs are:

- Rank 254: `session-sample-27-rollout-7-4e0fccd7110647398f9744d49baae933`.
- Rank 255: `session-sample-27-rollout-0-eaef2f9788304c87add495ac9cc066c0`.
- Rank 256: `session-sample-27-rollout-4-ae6a2459e18b423ea768ada006060f6e`.

They made 54, 46 and 69 model roundtrips, respectively. Their first model requests began around **19:32:45–19:33:30 UTC**, after roughly 23 minutes of create-gate waiting. For the final runner, **406.075 seconds—91.1% of its remaining 445.768 seconds—were model roundtrips**. That measurement includes gateway/backend queueing, network transport and generation. It does not split model compute from communication or prove that the GPU kernels were the limiting component.

Accounting uses paired monotonic spans from each runner. Startup runs from episode entry to the first model query and includes the native initial working-directory tool call; that call is assigned once to startup. Model, later logical-tool, verifier and cleanup intervals are checked for cross-category overlap. Nested gateway spans, SDK calls and native verifier execution timers are not added to their parent totals. All observed spans for these twelve runners pair correctly, with zero cross-category overlap. The largest paired wall-clock/monotonic discrepancy was **0.111 ms**, and result/profile lifetime differences were below **0.136 ms**.

Unattributed time is retained: **1.143 seconds** outside the broad categories across the complete lifetimes, plus **12.256 seconds within the startup envelopes** not covered by the selected startup-operation spans. The latter is already included in startup. The final-window unattributed remainder is **0.199 seconds**. Recorder health-status files were unavailable for these twelve runner processes, so this is not an independent certification of zero dropped events. No model prompts, tool arguments or raw outputs were needed for the accounting.

The completed native **training step 1** had a longer boundary than runner collection:

| Native phase | Wall time |
|---|---:|
| Generation (`timing_s/gen`) | **3019.421 s** |
| Old-policy log probabilities | **108.931 s** |
| Actor update | **440.557 s** |
| Checkpoint save | **516.351 s** |
| Other, including advantages, weight synchronization and residual overhead | **7.906 s** |
| **Native step total** | **4093.166 s (68.22 min)** |

The checkpoint manifest records **427,535,690,122 bytes, or 398.17 GiB**, across 40 files. Runner completion, optimizer update, checkpoint saving and subsequent reload are separate milestones. [Native metrics](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/train-metrics.jsonl) · [Step-1 checkpoint/status metadata](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/supervision/20260920T184935Z-3c9aeb9d/status.json).

R2 has resumed from step 1. This first-batch analysis does not claim completion of the 100-step run or predict the benefit of changing any gate, model, or execution setting. [Current status](STATUS.md).
