# R2 startup diagnostics

This inspection found **three distinct startup problems**: one guest package-manager lock, two server-side envd readiness failures, and ten client-side shell initialization deadlines. The ten shell initializations recovered through their existing retries. No persistent orphan from either failed create was observed in the later checks. The repaired admission actor remained consistent and continued making progress.

These observations do not establish completion of R2's full logical batch, a native optimizer update, or checkpoint acceptance. [Current RL status](STATUS.md).

The profile counts below use **training-phase events ending by 2026-09-20 19:31:00 UTC on the training host**. They exclude validation and unfinished spans. Server logs and VM samples use the VM clock; matching template IDs and request durations support the correlations below, but no shared request ID was recorded for the failed SDK creates.

| Measurement at the profile cutoff | Observed count |
|---|---:|
| Completed SDK create attempts | 203: **201 successful, 2 errors, 0 cancelled** |
| Completed tmux package-install attempts | 198: **197 successful, 1 nonzero exit** |
| Completed toolbox initializations | **188 successful** |
| Distinct shell initializations that first timed out, then recovered | **10** |

These are attempt/initialization counts, not completed training episodes or a failure rate for the full 256-rollout batch. [Metadata-only profile summary and trace references](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/startup-diagnostics-20260920/profile-summary.json).

**Package-manager contention.** At **19:17:32.274 host UTC**, session `session-sample-14-rollout-0-8f70ffbf660e43dda4362b7f80327aa4`, task `monarch-initiative__koza-60`, failed while installing cached tmux packages in sandbox `i4ivg93rd6bb3c7jtujuz`. `dpkg` exited **2** after 1.619 seconds and reported that guest PID **1520** held the frontend lock. The captured evidence does not identify that process's program; attributing it to an automatic updater would be speculative. This was a package-install conflict after sandbox creation, not an API placement failure. The failed instance was cleaned up and the native sandbox-start retry re-entered admission. Its cgroup was last observed at VM 19:17:31 and was absent in subsequent samples. This cutoff does not claim that the later replacement startup had already succeeded.

**The two HTTP 500 creates reached the envd readiness deadline.** The scoped API logs contain the nested server errors:

| SDK session and task | SDK error time / duration | Matching API event, VM UTC / handler duration | Server sandbox |
|---|---|---|---|
| Sample 17, rollout 6; `repobee__repobee-336` | 19:22:05.363 / **25.932 s** | 19:22:04.253 / **25.890 s** | `ipilaezwjzv867xa9poak` |
| Sample 22, rollout 6; `tox-dev__tox-1099` | 19:26:48.720 / **18.121 s** | 19:26:47.608 / **18.078 s** | `iz9joqt704r31i05rvj8w` |

Both API errors report `failed to wait for sandbox start → failed to init new envd → context canceled with cause: syncing took too long`. This is the runtime's overall `WaitForEnvd` initialization/readiness deadline. It does not isolate guest clock synchronization or identify the particular boot, transport, I/O, or envd sub-operation that was delayed. The template IDs match the SDK sessions; the timings and durations closely agree, but these remain correlated requests rather than an exact request-ID join.

The Repobee failure additionally reported **“fc process 599949 still exists after SIGKILL.”** That historical shutdown-delay error is preserved. At a read-only check on **19:41:26–27 host UTC**, PID 599949 was absent from the orchestrator container's process namespace, so its start ticks could no longer be read. Both failed sandbox IDs returned HTTP **404** from the API. Their cgroups were absent from the latest VM samples: Repobee was last seen at VM 19:22:03.896, and Tox at 19:26:46.561. Thus no persistent failed-create microVM orphan was observed at that later checkpoint; the transient stop delay is not being reclassified as a successful immediate shutdown. [Presence checks](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/startup-diagnostics-20260920/failed-create-presence.json).

The VM windows surrounding those two failures showed CPU peaks of **50.9% and 76.5%**, CPU PSI `some avg10` peaks of **32.48% and 20.00%**, and I/O PSI peaks of **1.94% and 0.15%**. Memory PSI stayed zero, available memory remained above approximately **1.40 TiB**, NBD attachment peaked at **99 and 111 of 1,024**, and no new ARP table-full increments were recorded. These indicate scheduling pressure without an observed memory/NBD-count/ARP exhaustion boundary. They do not establish the cause of either individual envd timeout.

The API capture covers the requested window. Available orchestrator logs instead began at **19:36:11**, after both failures, so their detailed init retries and failed-process state were no longer available in that capture. Client-proxy capture returned no lines. Absence of selected records is not evidence that those components had no errors. [Capture boundaries and hashes](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/startup-diagnostics-20260920/capture.json).

**Shell initialization exhausted a different, 60-second budget.** Ten sessions hit their first per-tool startup deadline between host **19:25:30 and 19:28:05 UTC**. In nine, cancellation occurred while awaiting the shared exec gate; in one, it occurred during an SDK command after admission. Their first attempts lasted **60.019–60.100 seconds**, of which **52.417–58.815 seconds** were measured exec-gate waiting. All ten eventually completed toolbox initialization successfully, with total initialization times of **101.259–174.074 seconds**, including retry/backoff.

Native `Toolbox.__aenter__` starts each tool with a 60-second `asyncio.wait_for` budget and doubles that budget on retry. The shell's initialization performs several commands through the shared exec gate, so queueing consumes this budget. R2's **1,800-second sandbox startup budget** is separate: increasing it did not change the tool's 60-second first-attempt deadline. The observed cancellations therefore should not be described as 60-second E2B command execution failures.

The admission heartbeat at **19:31:03 host UTC** reported consistent create and exec pools. The exec pool had 16 active permits, 72 queued requests, and nine cancellation calls; its capacity-progress age was about **0.10 seconds**. Together with the ten successful retries, this supports continued permit turnover, not recurrence of R1's unreconciled cancelled-acquire leak. It does not guarantee future progress under every load. [Cancellation protocol and validation](../phase5-optimization/GATE-CANCELLATION.md).

**Recovery follow-up, 20:05 host UTC.** The three sandbox-start failures also recovered under the existing retry policy. Repobee and Koza received replacement sandboxes at 19:36:34; Tox at 19:36:57. Their package installation and toolbox initialization subsequently passed, and all three task results report `completed` with `finished=true`. These later outcomes do not change the 19:31 cutoff denominators above. At 20:05:37, the actor reported all **3 create and 16 exec permits available**, zero queued/active tickets, and consistent pools. This is admission and episode recovery evidence; native full-batch update/checkpoint acceptance remains a separate requirement.

No running configuration, frozen source, sandbox lifetime, package lock, or service was changed during this inspection. Read-only log/process/API checks created no sandboxes. Raw captured logs remain private; this report contains metadata and filtered error descriptions only.
