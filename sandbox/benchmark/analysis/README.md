# Benchmark analysis

This directory contains reporting code, not workload drivers. It performs no network requests and does not start, modify, or delete sandboxes. Input must come from an actual measurement; running with empty inputs is an error.

Aggregation uses the Python standard library. PNG/SVG figures additionally require `matplotlib` in the isolated benchmark environment chosen by the runner owner. No packages are installed by this utility.

```bash
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python \
  /home/yuhanya/Daily_LLM/sandbox/benchmark/analysis/summarize.py \
  /path/to/measured/operations.jsonl \
  --output /path/to/phase1-service/report
```

Supply several JSONL files to aggregate repeats or phases. Directory inputs are searched recursively; prefer explicit operation/result files so resource-sampling files need not be scanned. `--phase phase1-service` filters a combined input. `--no-plots` produces tables using any Python 3.9+ interpreter. The output path is explicit, so reports can live in their respective stage folders. Run reporting only after raw data has been finalized.

Final campaigns should freeze an explicit file list with size/SHA-256 before supplemental runs start, then pass only those listed files. Plotting defaults to curves with multiple measured points; `--include-single-point-plots` restores isolated point charts when useful. Open-loop plots separately expose scheduled arrivals that were not dispatched, so a zero error rate among dispatched requests cannot imply every arrival was served.

## Input contract, version 1

Each line is an object with `record_type: "operation"` or `record_type: "run"`. An operation record represents one **logical operation**, with its full experience including any retries. Do not also feed physical-attempt records as logical operations. Other record types are skipped and counted. Strings identifying requests and runs must be unique within their stated scope. Repeat IDs identify independently executed runs, not shards of one run.

The frozen 2026-09-20 runner reuses operation indices between warmup and measurement. For these records, parser identity is explicitly `(run_id, window, operation_id)`, and a cross-window reuse warning is emitted. Raw IDs and input files remain unchanged. Duplicate IDs within the same run/window remain a hard error. This normalization does not merge warmup latency into measurement statistics.

The final run record and operation records share configuration metadata. Metadata can be inherited from the final run record; repeated metadata must agree.

| Field | Meaning |
|---|---|
| `run_id` | Unique scenario/configuration/repeat execution ID |
| `phase` | `phase1-service`, `phase2-sdk`, or another explicit phase |
| `scenario` | Versioned workload contract; equal values across phases assert matching semantics |
| `template` | Template/build identity, not an undocumented friendly alias |
| `memory_mib` | Configured sandbox memory; this does not assert actual RSS |
| `resident_count` | Resident sandbox population; needed for resident/cross-phase plots |
| `concurrency` | Configured client in-flight concurrency |
| `mode` | E.g. `closed_loop`, `open_loop`, `serial` |
| `instrumentation` | Recorded observation mode, e.g. `minimal` |
| `repeat` | Independent repetition ID |
| `measurement_kind` | `finite_batch`, `steady_state`, or another explicit design |
| `comparison_key` | Optional stronger assertion of identical workload semantics across differently named scenarios |
| `arrival_rate_ops_s` | Configured open-loop arrival rate; separate rates are never collapsed into a repeat group |
| `payload_bytes`, `page_mode`, `throughput_scope` | Additional comparison/grouping fields where recorded; lifecycle cycle throughput must remain distinct from per-call latency |

All fields through `repeat`, except `resident_count`, are required. `measurement_kind` and `resident_count` are required for cross-phase plots. A workload key should include meaningful input/payload, tool/session, cache, retry, timeout, and connection-reuse policies, or refer to an immutable manifest defining them. Changing these policies requires a new key. Keep endpoint, SDK/runtime versions, page mode, input identity, generator placement, and environmental interference in the run manifest as well.

Operation records additionally require:

| Field | Meaning |
|---|---|
| `operation_id` | Unique within the run, shared with traces if available |
| `operation` | Operation class, e.g. `create`, `exec_true`, `file_read_1k`, `file_write_1k`, `files1m`, `mixed` |
| `window` | `measurement`, `warmup`, or another explicit cohort; defaults to `measurement` |
| `start_offset_s`, `end_offset_s` | Dispatch/completion offsets from the **same run's monotonic origin**, in seconds |
| `duration_ms` | Complete logical-operation duration in milliseconds; must agree with offsets |
| `status` | `success`, `error`, `timeout`, `rejected`, `cancelled`, or `invalid` |
| `state_check` | Boolean correctness/validity result when applicable; false overrides nominal success |
| `attempt_count` | Positive integer, initially 1; includes retries |
| `planned_offset_s` | Scheduled arrival from the same origin; required to assess open-loop dispatch lag |
| `first_output_ms` | Optional delay from dispatch until first output |
| `error_class` | Optional specific reason; do not include credentials or full sensitive payloads |

Optional fields such as endpoint, byte counts, execution ID and manifest references are permitted. They remain available in raw data even if not aggregated by this initial utility.

Each final `run` record provides `measurement_start_offset_s`, `measurement_end_offset_s`, `scheduled_count`, `dispatched_count`, `completed_count`, `cleanup_ok`, and `stop_reason`. The explicit positive window is the throughput denominator; it is never inferred from summed latencies. Counts describe **all measurement operations in the run**, not one operation class. `dispatched_count` must match logical operation records dispatched in that interval. Emit terminal records for failed, rejected, timed-out, and cancelled requests too. If dispatch accounting is incomplete, the report retains the run and leaves failure rates null rather than silently treating missing operations as success.

The producer's final counters are reconciled against all records explicitly labelled `window: "measurement"`; exact timestamp boundaries separately determine the performance cohort. The supplemental buffered SDK run exposed a dispatch 1.798 microseconds after its declared end because its worker deadline was computed separately. Such records are not missing data: `measurement_labeled_operation_count`, `outside_window_dispatch_count`, and `outside_window_failure_count` retain the distinction. Their timestamps are unchanged, strict-window performance statistics exclude those starts, and terminal errors remain in the error evidence table. This avoids both widening a measured window and silently losing an out-of-window outcome.

In open-loop mode, `scheduled_count` counts arrivals actually scheduled inside the measurement window, including dropped/undispatched arrivals. In a finite serial script it may instead denote the planned operation budget, including dependencies never reached after an early failure; the utility therefore derives an offered arrival rate only for open-loop runs. `completed_count` counts all terminal records, including failures and drain completions; use a separate `successful_count` for successes only.

Failed setup attempts require special handling: the frozen v1 runner can emit a configured-duration window even when setup fails before any measured operation. A non-completed run with zero measurement operations, or an explicit `measurement_started: false` / `measurement_valid: false`, is ineligible for performance aggregation. The run remains in `runs.csv/json` with `performance_eligible: false`, its stop reason, and its original window under `reported_measurement_*`; the normalized measurement window/rates are null. It produces no zero-throughput capacity point. The resource reporter applies the same guard and excludes that window from per-run aggregates, retaining the whole capture as setup diagnostic evidence. A completed retry is analyzed separately; the failed setup is never silently removed from the attempt record.

## Statistical definitions and limits

- **Throughput:** successful or all terminal completions whose end offsets fall inside the explicit window, divided by window duration. The runner should consistently tag the measurement cohort. Drain completions remain in latency statistics but are not counted inside an earlier steady-state throughput window.
- **Latency:** successful measurement-cohort operations dispatched inside the window, including their drain completions. Failure counts/rates are reported alongside it. Scheduled-arrival-to-completion quantiles and dispatch lag are separate fields when schedule timestamps exist.
- **Mixed-script rates:** each operation class is divided by the entire script's window and marked `contextual_operation_rate`. A create rate within a create/exec/delete script is not standalone create capacity. Such rates are not compared to isolated-operation rates.
- **Quantiles:** linear interpolation using index `(n - 1) × p` (R-7). Single-run quantiles are descriptive. P99 has a warning below 1,000 successful observations per run; this is an evidence threshold, not a guarantee that 1,000 observations establish a stable tail.
- **Repeat aggregation:** median of per-run metrics and observed run minimum/maximum. Latencies are not pooled across runs. Min/max shading is not a confidence interval. A missing metric in one run makes the corresponding aggregate metric null; incomplete runs do not disappear from the estimate.
- **Failure rates:** include errors, rejection, timeout, cancellation, and invalid results in the recorded dispatch cohort. Expected nonzero command exits should already be classified by the workload's correctness contract. SDK retries affect logical latency and `attempt_count`; attempt success is not logical-operation success.
- **Cross-phase comparisons:** require matching scenario or explicit comparison key, operation, template, configured memory, resident count, concurrency, mode, instrumentation, measurement kind, and compatible contextual-rate status. Comparisons require complete dispatch accounting. No comparison is inferred from unmatched experiments, and no numerical difference is automatically attributed to SDK overhead.
- **2026-09-20 transport caveat:** the thin service adapter uses Python HTTPX, while the SDK transport uses pyqwest. A matched request comparison therefore includes client transport and generator costs. It is not a measurement of intrinsic service capacity or isolated SDK overhead. Authentication/header behavior belongs in the frozen workload contract; shared secrets must not be logged.
- **Capacity:** finite batches, limited samples, and curves without frozen SLOs do not establish sustainable capacity. These reports do not claim RL speedup, full-memory sandbox capacity, or training correctness.

## Outputs and quality checks

`runs.csv/json` retains whole-run accounting and cleanup. `per_run.csv/json` contains each operation/configuration/repeat. `summary.csv/json` contains repeated-run aggregates. `data_quality.json` lists limited tail evidence, missing state checks, missing final windows, accounting problems, cleanup failures, and schedule gaps. `inputs.json` records input paths and SHA-256 hashes. `REPORT.md` links plots indexed by `plot_index.json`.

`errors.csv/json` preserves terminal failed operations, timing, error class, and RPC code. An umbrella `error` status can contain `TimeoutException`; class names do not by themselves establish elapsed timeout budgets, connection root causes, or sandbox TTL expiry. Logical file-payload rates remain separate from measured HTTP entity or network bytes.

The utility rejects malformed JSON, duplicate operation/run IDs, contradictory configuration, impossible timestamp ordering, duration unit mismatches, invalid retry counts, and unknown terminal statuses. It keeps failed experiments rather than excluding them. Missing final summaries retain latency observations but do not acquire an invented throughput window. Missing resource samples are not replaced or interpolated by this utility.

Figures include concurrency/throughput, successful P50/P95/P99 and failure rate, resident-population anchors where there is more than one observed population, and eligible matched phase comparisons. Each figure is exported as PNG and editable-text SVG. A single observed point is a measured point, not an interpolated capacity curve. No unmeasured points are added.

When the measured matrix has `C = N` at several points, an additional combined concurrency/population figure shows that diagonal. It explicitly states that both variables change; it does not replace a fixed-N concurrency sweep or establish which variable caused a change.

For `create_delete_churn`, the frozen runner's raw `resident_count` is an unused CLI default: this scenario does not prepare a resident pool. Derived per-run/summary tables set `resident_count` to null with `resident_count_applicability: "not_applicable"` and preserve the original value and explicit provenance separately. A quality notice records this normalization; source JSONL is unchanged. Lifecycle charts omit N and label concurrency as cycle workers. At most that many create/delete cycles are active; the count is not a measured resident population. Matched lifecycle comparisons may therefore omit N while still matching the other workload/configuration fields.

## Resource capture reports

`resources.py` consumes the finite foreground sampler's `metadata`/`sample`/`completion` JSONL. It keeps `host` and `guest` (the outer E2B VM) separate. It emits resource samples, summaries, interval coverage, quality findings, and a separate PNG/SVG timeline for each layer/capture. It neither merges physical-host costs with guest costs nor attributes other host activity to E2B.

```bash
/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python \
  /home/yuhanya/Daily_LLM/sandbox/benchmark/analysis/resources.py \
  /path/to/finalized-resource-capture.jsonl \
  --windows-jsonl /path/to/finalized-operations.jsonl \
  --output /path/to/phase1-service/report/resources
```

Run association is optional. The operation JSONL's final run records must provide either `measurement_start_utc` and `measurement_end_utc`, or `run_started_utc` plus the measurement offsets. `run_started_utc` must identify the same origin as monotonic offset zero. Cross-machine UTC association depends on synchronized clocks and should be interpreted at the sampling resolution. No monotonic timestamp from one machine is subtracted from another machine's timestamp.

Only intervals wholly contained in a measurement window enter its resource summary. Intervals that cross a boundary are excluded and counted; the tool does not assume uniform work inside such intervals to prorate their rates. Without UTC measurement windows, summaries describe the whole capture, including any setup, warmup, or cleanup it contains. Coverage is reported per layer, capture, and run; missing intervals and absent per-device values are never filled with zero.

CPU, byte rates, IOPS and queue/busy rates use actual `interval_seconds` as weights. Disk await uses I/O operation count as its weight. Already differenced error/drop/event counters are summed, then divided by observed interval time if a rate is needed. Gauges such as memory/FD counts report sampled min/max/median; they are not presented as exact time integrals. PSI `avg10` retains its original smoothed meaning. Cgroup CPU/throttle fields cover continuously observed cgroups only. Inactive NBD devices omitted by the sampler stay omitted, with per-metric coverage in the summaries.

Use repeated `--plot-capture-path /exact/raw/capture.resources.jsonl` options to limit figures to representative captures while retaining every supplied capture in summaries. `--no-samples-csv` avoids duplicating the complete sampled metric table in the documentation repository; raw captures and hashes still support reconstruction.

## First-call diagnostics and the frozen main report

`first_call_cohorts.py <finalized.jsonl> [...] --output <report-directory>` separates each worker's earliest measurement record, identified by `start_offset_s`, from its later records. It emits per-run and pooled-count CSV/JSON plus PNG/SVG figures. Warmup is excluded; "first measured" does not assert the first-ever SDK operation because setup can precede it. The comparison is descriptive and does not establish a network or runtime root cause. Figures explicitly identify their different vertical scales and show failure/request counts.

`write_main_reports.py` is the campaign-specific 2026-09-20 report builder. It consumes already-derived phase reports and `INPUTS-FROZEN.json`, verifies their input hashes, writes compact Chinese `MAIN-RESULTS.md` drafts and resource/run joins, and records analysis/executed-source provenance. It does not discover new supplemental input files or execute workloads. Its reports retain the three failed setup attempts and N=256's two measured repeats, and distinguish the main matrix from later controls.

Tests use tiny, explicitly artificial in-memory fixtures solely to verify accounting and statistical edge cases. They do not produce benchmark results or figures:

```bash
python3 -m unittest discover -s /home/yuhanya/Daily_LLM/sandbox/benchmark/analysis -p 'test_*.py'
```
