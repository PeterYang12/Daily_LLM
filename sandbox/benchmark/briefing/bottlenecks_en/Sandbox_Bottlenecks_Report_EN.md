# Sandbox Bottlenecks in Agentic RL

**Mechanisms, concrete examples and profiling targets**

Pure CPU computation is not always the main cost. For short agent tool calls, process creation, system calls, filesystem activity and preparing a clean environment can outweigh the useful computation. The categories below are examples to investigate, not seven confirmed diagnoses from our E2B run.

| Bottleneck | Example in an agent workload | What to inspect |
|---|---|---|
| CPU throttling | A build exhausts an enforced CPU-time budget and waits for the next quota period, even with host cores idle. | cpu.max, throttled_usec, nr_throttled; distinguish CPU quotas from vCPU count. |
| Cold start and reset | Recreating a clean sandbox for every short Python action repeats readiness and runtime setup. | Create → ready → first command; dependency/cache state; fresh-state reset time. |
| System calls and process creation | Hundreds of tiny subprocesses spend time on launches, pipes, scheduling and I/O. | Process-start latency, syscall counts, context switches and relevant VM exits. |
| Filesystem I/O | Package installs, repo scans and patches touch thousands of small files. | Metadata latency, IOPS, cache misses and copy-on-write effects; control compression. |
| Memory pressure | Parallel test workers increase working sets until reclaim, allocation stalls or OOM disrupt execution. | Working set, memory PSI and memory.events; swap only if configured. |
| Resource contention | Simultaneous launches and tool calls compete for CPU, caches, memory bandwidth and storage. | p95/p99, queues, per-core CPU and I/O pressure; compare isolated and shared load. |
| Compatibility limitations | A debugger needs ptrace; Docker or nested KVM may need missing privileges or /dev/kvm. | Functional preflight for required syscalls, kernel features, packages and devices. |

## Observations from the local E2B study

- **First-use cost:** first run_code P50 **1,027 ms**, second **68 ms**, across 60 paired sandboxes. This does not isolate SDK, syscall or process-start cost.
- **Startup reliability:** the target-256 resident-pool experiment had **2 measured runs and 3 preparation failures** out of 5 attempts. Readiness timeouts coincided with CPU bursts; a unique cause and hard capacity limit remain unproven.

## Important distinctions

Firecracker executes normal Linux syscalls in its guest kernel; it does not universally intercept every syscall in a separate security layer. Pause/resume preserves state and is not a clean reset. CPU-quota throttling and OOM were not established by this study, and the tested VM had no swap. Compatibility examples are deployment-dependent and were not benchmark findings.

The measurements are from a local Firecracker deployment and a minimal Code Interpreter template. They are short synthetic tests, not measured real-RL training speedups. Before attributing a bottleneck, validate workload correctness, timing boundaries and load-generator headroom.

Sources: [Full study](../../REPORT.md), [Application results](../../phase2-sdk/APPLICATION-RESULTS.md), [Startup evidence](../../phase1-service/results/setup-failure-log-20260920.md), [Lifecycle results](../../phase1-service/LIFECYCLE-RESULTS.md).
