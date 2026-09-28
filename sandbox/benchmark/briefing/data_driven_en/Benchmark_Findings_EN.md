# Benchmark findings: measured limits and next experiments

2026-09-20 UTC. Companion to the six-slide English briefing. Results describe the local deployment and tested workloads.

**Phase 0: file size does not identify transport cost.** Each file contained 1,048,576 logical bytes, with verified content:

| Content / encoding | Raw HTTP entity bytes |
|---|---:|
| Repeated `x` / gzip | 1,055 |
| Repeated `x` / identity | 1,048,576 |
| Deterministic random / gzip | 1,048,919 |

SDK counters can describe decompressed content. These results cannot be reported as network bandwidth. Buffering records changed service throughput by +0.9% and SDK throughput by −6.8%; sequential, confounded comparisons do not establish instrumentation overhead. [Phase 0 evidence](../../phase0-environment/REPORT.md).

**Phase 1: creation waiting increases while cycle throughput plateaus.** Each point has three runs; throughput includes deletion, whereas latency measures create only.

| Create→delete workers | Successful cycles/s | Create P95, seconds |
|---:|---:|---:|
| 1 | 3.64 | 0.455 |
| 2 | 5.51 | 0.395 |
| 3 | 5.88 | 0.537 |
| 8 | 5.47 | 2.915 |

Startup admission remained three. This configuration alone does not establish the bottleneck's cause. Only **2/5** N256/C8 pool preparations reached measurement; three failed during startup. An independent ramp limited to one create/second failed on request 117 after 116 successes. Neither observation establishes a fixed population limit. [Service matrix](../../phase1-service/MAIN-RESULTS.md), [slow-ramp diagnosis](../../phase1-service/IDLE-POOL-RESULTS.md).

**Phase 2: creation response precedes application readiness.** Across 60 new sandboxes, create P50 was **269.60 ms**, first `run_code` **1026.74 ms**, and second `run_code` **68.39 ms**. The paired, directly measured create-start→first-result P50 was **2155.13 ms**; it includes an intervening command/health check. Second execution was faster in 60/60 pairs. These are local minimal-template results. [Application evidence](../../phase2-sdk/APPLICATION-RESULTS.md).

Separately, C=N64 command failures concentrated in each worker's first **measured** call: **18/192 (9.375%)**, versus **1/171,318 (0.000584%)** later. These are not necessarily first-ever SDK calls. Exercising every slot at C8 beforehand still left 9/192 first-measurement failures at C64. [Main results](../../phase2-sdk/MAIN-RESULTS.md), [preparation control](../../phase2-sdk/SUPPLEMENTAL-RESULTS.md).

At fixed total concurrency/population 64, changing one generator process to four raised protocol throughput **312.90→1946.95 ops/s** and SDK throughput **1956.60→2474.54 ops/s**. Process, connection-pool, and preparation organization changed together. This demonstrates harness sensitivity, not a service optimization or isolated CPU effect; errors remained. [Generator control](../../phase1-service/MULTIGENERATOR-RESULTS.md).

| Category | Evidence status | Next experiment |
|---|---|---|
| CPU throttling | **Not established:** slow ramp recorded zero throttle increments; CPU PSI peaked at 22.43%. | Align scheduler, cgroup, and failed-guest thread traces. |
| Cold start / reset | **Demonstrated symptoms:** first-use delay and startup/connection failures; no clean-reset benchmark. | Trace Firecracker readiness, envd initialization, first connection, and interpreter startup separately. |
| Syscalls / process creation | **Not established:** end-to-end command timings do not isolate fork/exec or syscall costs. | Compare process reuse with fresh execution; collect syscall/CPU profiles. |
| Filesystem I/O | **Measured workload sensitivity; storage bottleneck unproven.** Encoding/content affect the measured path. | Hold content fixed, vary encoding, and record entity bytes plus storage counters. |
| Memory pressure | **Not established:** slow-ramp MemAvailable stayed above 1464.17 GiB. | Measure RSS, faults, reclaim, and memory PSI during controlled population growth. |
| Contention | **Correlated** CPU pressure and startup failure; generator controls demonstrate harness sensitivity. | Isolate client scaling, startup admission, and guest scheduling in separate controls. |
| Compatibility | **Demonstrated only within scope:** 240 application stages and 120 state-preserving pause/resume rounds passed. | Replay representative packages, languages, and tool actions. |

State-validation source: [lifecycle results](../../phase1-service/LIFECYCLE-RESULTS.md).

**Infrastructure and routing.** Two AMD EPYC 9965 sockets provide 384 physical cores, SMT off. Generator affinity is 0–15, analysis 16–31, QEMU 192–383. The outer VM has 192 vCPU and 1486.84 GiB visible RAM; typical guests have 2 vCPU/4096 MiB, ordinary 4 KiB backing, and no HugeTLB pool. Running quota is 512, startup admission three. SDK versions are 2.49.1 / Code Interpreter 2.10.0. Nginx at `10.145.64.67:8088` routes `E2b-Sandbox-Id` requests through client-proxy to guest envd; other requests go to API→orchestrator→Firecracker. [Inventory and scope](../../REPORT.md), [deployment](../../../../RL/uni-agent-kickoff/10-local-e2b-vm-k8s/README.md).

Data-plane windows last 20–30 seconds, usually with three repetitions. Host/VM clock alignment has roughly one-second uncertainty, preventing precise cross-layer resource attribution. No full RL run, training acceleration, stable SLO, or long-duration capacity claim is established.
