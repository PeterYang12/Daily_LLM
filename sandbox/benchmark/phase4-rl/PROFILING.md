# Real RL profiling for the 100-update pathfinding run

Active implementation root: `/data2/yuhanya/e2b-rl-pathfinding-20260920-r2`. The container mounts it at `/experiment`, with `UA_EXPERIMENT_ROOT=/experiment` and `UA_RUN_ID=ua-e2b-pathfinding-20260920-r2`. Profiling records metadata locally; ordinary private trajectory logs remain under the run directory. Current counts and read-only inspection commands are in [STATUS](STATUS.md) and [README](README.md).

The R1 paths in the collector/replay command examples below are retained historical setup examples. R2 training and its collectors are already active; these examples are not commands to restart them. This document describes instrumentation, not completion of the 100-step target.

The previous Qwen3-Coder-30B-A3B run completed one optimizer update in 2,119.44 seconds. An unchanged 100-step extrapolation is about 58.9 hours and is only a planning reference. Count actual completed native trainer steps, inspect per-rank successful optimizer calls, and verify the committed final checkpoint independently. Agent turns and completed rollouts are different counts.

## Instrumentation and boundaries

| Source | Recorded boundary | Interpretation |
|---|---|---|
| `profile_events.py` | UTC and monotonic begin/end events, process and parent IDs | Duration is local monotonic time. An unmatched begin is unfinished work. |
| `run_runner.py` + `profile_runtime.py` | Episode, native ReAct agent, logical `Toolbox.call`, model query, native verifier, repository setup | Nested spans retain their parent; cumulative parallel durations are not training wall time. |
| `e2b_provider.py` | Create/exec gate wait; create acknowledgement; readiness attempts/backoff; tools probe/read/upload/install; file read/write; complete SDK command; kill attempts/backoff | SDK command ends after the SDK returns with consumed output. Gate wait is separate. Nonzero command exit is data, not automatically a transport error. |
| `runtime_observe.py` | Every native optimizer call, rank, success and finite gradient; first-call parameter probes | Counter is not assumed equal to global trainer step. At most three 256-value probes/rank, first call only. GPU-to-CPU probe copies may synchronize that first call. |
| Native verl timers + `install_trainer()` | `gen`, logprob, advantage, actor update, weight sync, step and checkpoint | Native timers define step wall time; checkpoint wrapper supplies absolute boundaries. |
| `profile_monitor.py` | Five-second node CPU/PSI, owned process CPU/RSS/FD, selected cgroups, AMD sysfs busy/VRAM, optional local vLLM counters | CPU-only collector imports no torch/GPU runtime. Whole-node and unassigned GPU counters include other workloads. |
| Existing phase-0 `sample_resources.py` | Physical host + outer VM CPU/PSI/disk/NIC/NBD/FD/neighbour and sandbox cgroups | Finite foreground SSH sampler; old systemd monitors remain disabled. |

The provider baseline retains three create permits through readiness and tools preparation, and sixteen exec permits. Training and validation use the same admission actor. It does not change timeouts, retry arbitrary agent commands, or preinstall tools as a profiling side effect.

`UA_PROFILE_SEPARATE_CREATE_GATE=0` is the baseline. A later explicitly selected experiment may set it to `1`, releasing the create permit immediately after SDK create returns. Setup still executes; the event field `gate_policy` records `create_and_setup` or `create_only`. Compare separate runs with matched tasks/configuration. Do not mix policies within the baseline's reported 100 steps.

`profile_runtime.install()` is idempotent and runs in each relevant worker. The learner process calls `profile_runtime.install_trainer()`. Gateway model round-trip timings still include preparation, service queueing, inference, transport and retry delays; they are not pure GPU decode. The optional vLLM metric scrape provides interval aggregates, not per-request queue/prefill/decode splits. Hidden SDK transport retries are not fully enumerated, so SDK spans count logical calls.

## Finite observers

Run the host collector with the actual owned training host PID. It pins PID start ticks and follows descendants, including already observed descendants whose parent exits. Alternatively supply an explicit dedicated training cgroup; the root host cgroup is rejected. Never use an interactive user shell as the ownership root.

```bash
python3 -B /data2/yuhanya/e2b-rl-pathfinding-20260920/code/profile_monitor.py \
  --output /data2/yuhanya/e2b-rl-pathfinding-20260920/results/resources-host.jsonl \
  --stop-file /data2/yuhanya/e2b-rl-pathfinding-20260920/run/STOP_OBSERVER \
  --pid TRAINING_HOST_PID --interval 5 --samples 17280
```

The PID placeholder must be replaced before execution. Add `--cgroup /sys/fs/cgroup/DEDICATED_GROUP` if necessary. Repeated `--gpu-pci 0000:XX:YY.Z` restricts GPU sampling to explicitly assigned devices; without it GPU records are labeled node-wide. Repeated `--metrics-url http://127.0.0.1:PORT/metrics` samples each explicitly available model replica. The collector does not discover or create model endpoints.

The 17,280-sample budget covers approximately 24 hours at five seconds. Set a larger finite sample budget if the chosen training window requires it; maximum is 86,400 samples. Output is capped at 512 MiB by default, with a distinct capacity-reached exit. SIGTERM, SIGINT and the stop-file produce a completion record. A completed observer does not mean completed training.

Reuse the VM sampler without enabling any historical monitor service:

```bash
python3 -B /home/yuhanya/Daily_LLM/sandbox/benchmark/phase0-environment/sample_resources.py \
  --output /data2/yuhanya/e2b-rl-pathfinding-20260920/results/resources-vm.jsonl \
  --stop-file /data2/yuhanya/e2b-rl-pathfinding-20260920/run/STOP_OBSERVER \
  --interval 5 --samples 17280 --per-sandbox
```

Its configured SSH helper must already refer to this E2B VM. It reads all E2B sandbox cgroups, which must be joined with run-owned sandbox IDs before ownership claims. Per-sandbox memory is Firecracker/cgroup host accounting, not guest application memory. `memory.peak` is not reset. Capture bounded client/VM clock-offset observations at start and periodically; historical UTC disagreement was about one second, so received timestamps alone do not calibrate the VM clock.

## Storage and overhead

Span files are `results/profile/events-PID-START.jsonl`, mode 0600. The writer admits only fixed scalar metadata fields, strings up to 256 characters, and events up to 8 KiB. Default capacity is 128 MiB per process (`UA_PROFILE_MAX_MIB_PER_PROCESS`). It performs an append write per begin/end event, without per-event fsync. Exit status files report bytes, dropped events and write errors; SIGKILL can omit a status file. Abrupt process termination can therefore leave unmatched events and uncertain last-write durability.

This is a low-overhead design, not a measured overhead percentage. Record observer `sample_cost_ms`, `observer_cpu_seconds`, schedule lag, gaps and the first-step probe boundary. Historical recording comparisons did not isolate the cost of fully enabled vs disabled instrumentation. Expensive Python/kernel profilers and full OTel tracing are not enabled for all 100 steps. Current E2B telemetry has known missing-address and empty-metrics issues; empty results remain unavailable.

## Analysis and verification

```bash
python3 -B /data2/yuhanya/e2b-rl-pathfinding-20260920/code/profile_test.py
python3 -B /data2/yuhanya/e2b-rl-pathfinding-20260920/code/profile_analyze.py \
  --root /data2/yuhanya/e2b-rl-pathfinding-20260920 \
  --output /data2/yuhanya/e2b-rl-pathfinding-20260920/results/profile-analysis --phase train
```

Analysis uses the standard library; PNG/SVG rendering additionally uses matplotlib. `--no-plots` skips that dependency. It writes `spans.csv`, `sessions.csv`, `operations.csv`, `training-steps.csv`, `optimizer-ranks.csv`, `resources.csv`, `summary.json`, `quality.json`, and hashed input provenance. It accepts arbitrary actual step/session counts and preserves failed, cancelled and unfinished sessions. Native validation-only metric rows do not count as completed training steps. A per-rank optimizer-call counter is reported separately from completed trainer-step IDs.

Use `--phase train` for the RL baseline and `--phase validation` for evaluation. Every operation summary additionally groups by phase and `gate_policy`, even without a filter; P3 replay and P5 candidate-policy samples never enter the same SDK/gate percentile as baseline training. `phase-event-counts.csv` retains overall collection coverage. Resource samples retain their original whole-capture timestamps and workload scope; they are not automatically relabeled as train-only resource usage.

Gateway records without a reliable producer-side phase use `gateway_unknown`. Analysis may resolve those records only by an exact, unambiguous session ID join to a native runner result's phase. The original phase and join rule remain in `spans.csv`. Still-active or ambiguous sessions remain unknown, rather than being guessed from policy versions or environment defaults.

The initial figures show native step components, per-operation P50/P95, and host CPU/GPU time series. Operation figures use explicitly nested boundaries; their bars cannot be summed. `spans.csv` supplies exclusive local time by subtracting the union of completed direct-child intervals, and preserves raw parent IDs for subsequent critical-path analysis. Across processes, dependency links and bounded UTC alignment are still needed. Quality warnings and recording loss must accompany any throughput/tail conclusions.

The CPU-only tests exercise nested and failed spans, payload omission, baseline/opt-in permit ordering, nonzero command results, cancellation release, and partial-run counting. These checks do not exercise GPUs or E2B service capacity.

## Phase 3: fixed high-level action replay

`code/profile_replay.py` is an executable entrypoint. The historical 32-task/256-session report's CSVs contain derived timing, not complete tool arguments. Retrieve original transcripts or Task logs and audit their action/observation joins before constructing the private corpus. The original runtime path referenced by that report was absent locally during inspection. If another historical run supplies the corpus, record that distinct provenance; do not label it the 32/256 corpus.

Each JSONL row needs `instance_id`, `template_id`, `base_commit`, original `tool_specs`, `action_timeout_s`, `source_ref`, `source_sha256`, `completeness` (`complete` or `verified_prefix`), and ordered observed `actions`. Each action has `name`, exact `arguments`, and `expected_status`; optional observation hashes and `file_hashes_after` provide stronger state checks. Exclude declared-but-skipped calls and list those exclusions in the corpus manifest. Do not reconstruct missing file payloads from truncated low-level RPC logs.

Optional native final scoring requires `verify: true`, original task metadata without a gold `patch`, and `expected_resolved`. Optional `expected_patch_sha256` checks the final binary Git diff. Original template/build identity, cached tooling, repository setup and action timeouts must match the declared corpus. Full observation hashes may intentionally stop on nondeterministic text; status-only matches provide a weaker fidelity claim.

Audit only, with no runtime/SDK/credential import:

```bash
python3 -B /data2/yuhanya/e2b-rl-pathfinding-20260920/code/profile_replay.py \
  --manifest /data2/yuhanya/e2b-rl-pathfinding-20260920/data/replay-corpus.jsonl \
  --output /data2/yuhanya/e2b-rl-pathfinding-20260920/results/replay-audit
```

In the prepared runtime environment, execute with a fresh output directory:

```bash
python3 -B /experiment/code/profile_replay.py \
  --manifest /experiment/data/replay-corpus.jsonl \
  --output /experiment/results/replay-asap --concurrency 2 --schedule asap --execute
```

The runner creates a fresh owned E2B sandbox per episode, applies native repository preparation, constructs `Toolbox.from_specs`, initializes the same persistent tools, and calls each high-level action serially. First status/content/state divergence stops that episode. The async sandbox context cleans up on ordinary exceptions/cancellation; the run's independent cleanup supervisor remains necessary for process aborts.

ASAP removes only external inter-action gaps; it leaves sleeps and I/O inside commands unchanged. `--schedule recorded-gaps` requires measured `external_gap_before_s` on every action. It never substitutes historical durations with sleep to impersonate CPU/I/O work. No model is called. Results, throughput and verifier agreement characterize replay fidelity, not new model ability or a training speedup.

## R2 measured service and checkpoint windows

A bounded capture at the start of resumed batch 2 collected simultaneous **15-second Go CPU profiles** from the API and orchestrator at approximately **20:29:46–20:30:01 UTC**. All 19 bracketing admission observations showed an active create queue. The API accumulated **0.20 CPU-seconds** and the orchestrator **33.87 CPU-seconds**, equivalent to average use of about **0.013 and 2.26 CPU cores**. This window does not show API CPU saturation; it is an early-startup sample, not a peak-capacity measurement. Go CPU samples do not measure time spent blocked on I/O or guest syscall latency.

[Capture, ownership and timing metadata](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/step2-startup-cpu-profile/summary.json) · [Admission window](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/step2-startup-cpu-profile/admission-window.json) · [API CPU profile](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/step2-startup-cpu-profile/profiles/api.top.txt) · [Orchestrator CPU profile](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/step2-startup-cpu-profile/profiles/orchestrator.top.txt).

Two AMD-SMI snapshots at **20:11:29–35 UTC**, during checkpoint-1 saving, recorded **0% GFX/UMC activity** on all eight GPUs, **253–271 W** per device and **47–56°C** hotspot temperatures. XGMI traffic values were unavailable. These are checkpoint-phase observations; they do not diagnose the earlier model-compute phase. The usable CLI in the requested image is `/opt/venv/bin/amd-smi`. [Snapshot and library-warning evidence](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/amd-metrics-checkpoint-20260920/summary.json).

Native step 1 spent **516.351 seconds** saving its **398.17 GiB** checkpoint, including a measured **59.194-second** post-save file flush. A later short disk sample covered only 20:15:25–40, after the main optimizer writes; its low device write rate must not be presented as the average rate for the entire save. The completed native timer and checkpoint manifest are authoritative for the save boundary. [Brief disk sample](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/checkpoint-io-brief.json) · [Accepted checkpoint prefix](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/checkpoint1-prefix-20260920T184935Z-3c9aeb9d.json).
