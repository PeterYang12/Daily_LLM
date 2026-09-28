# Measured benchmark report

Recorded runs: **68**. Configuration/operation groups: **22**. Data-quality errors: **0**.

Attempts excluded from performance measurement: **3**. They remain in the run evidence and must be included when assessing setup reliability.

This report contains only recorded observations. It does not establish sustainable capacity or an RL speedup.

The 2026-09-20 thin service path uses HTTPX and the SDK path uses pyqwest. Cross-phase differences include client transport and generator costs. Diagonal C=N sweeps change concurrency and resident population together; separate fixed-resident anchors are needed to isolate their effects.

For create/delete churn, the raw resident_count is an unused CLI default and is preserved as provenance. Derived tables mark resident count not applicable, and lifecycle figures omit N. The configured concurrency counts create/delete cycle workers; throughput includes cycle pacing while create latency covers the create call.

Failed setup attempts with no measured operations stay in runs.csv/json with their stop reason and reported window preserved. Their window is not a performance denominator, and they do not produce zero-throughput capacity points. A completed retry contributes its own measured result; the failed attempt remains separate evidence.

- `runs.csv/json`: whole-run windows, record completeness, cleanup and stop outcomes.
- `per_run.csv/json`: operation statistics for each independent run and repeat.
- `summary.csv/json`: median of per-run statistics, run min/max and sample counts.
- `data_quality.json`: gaps and errors; failed runs remain in the data.

Throughput counts completions inside the explicit measurement window. Latency covers operations dispatched inside that window, including their drain completions. Mixed-script operation rates use the entire script window. Failure rates count all observed terminal non-success outcomes, including invalid results; rates are null when dispatch accounting is incomplete.

Successful-operation latency quantiles exclude failed operations and must be read with failure and completeness counts. P99 based on fewer than 1,000 successful operations per run is weak descriptive evidence. Run min/max bands show observed variation, not confidence intervals. Cross-run summaries do not silently omit runs with missing metric values.

errors.csv/json retains failed operation timing, error class and RPC code. Exception names and messages describe observed failures; a TimeoutException name alone does not establish an elapsed timeout budget or sandbox TTL expiry. File payload rates describe logical application bytes, not measured network bandwidth; compression and transport behavior require separate diagnosis.

Open-loop figures distinguish failures among dispatched operations from scheduled arrivals that were never dispatched. Zero service errors do not mean every scheduled arrival was served. The current lifecycle rate sweeps are finite batches whose elapsed window includes drain, not steady-state capacity certifications.

## Data quality

| Severity | Finding | Count |
|---|---|---:|
| warning | failed_setup_window_excluded | 3 |
| warning | incomplete_state_validation | 9 |
| warning | limited_p99_samples | 21 |
| warning | non_operation_records_skipped | 1 |
| warning | operation_id_reused_across_windows | 44 |
| warning | resident_count_not_applicable | 21 |

## Figures

- arrival-rate: phase1-service / create_delete_churn / create: [PNG](plots/arrival-rate-phase1-service-create-delete-churn-create-4b69e13ece.png) · [SVG](plots/arrival-rate-phase1-service-create-delete-churn-create-4b69e13ece.svg)
- concurrency: phase1-service / create_delete_churn / create: [PNG](plots/concurrency-phase1-service-create-delete-churn-create-466ef2617a.png) · [SVG](plots/concurrency-phase1-service-create-delete-churn-create-466ef2617a.svg)
- concurrency: phase1-service / exec_true / exec_true: [PNG](plots/concurrency-phase1-service-exec-true-exec-true-4e9991b45b.png) · [SVG](plots/concurrency-phase1-service-exec-true-exec-true-4e9991b45b.svg)
- residents: phase1-service / exec_true / exec_true: [PNG](plots/residents-phase1-service-exec-true-exec-true-935adb3663.png) · [SVG](plots/residents-phase1-service-exec-true-exec-true-935adb3663.svg)
- coupled-population-concurrency: phase1-service / exec_true / exec_true: [PNG](plots/coupled-population-concurrency-phase1-service-exec-true-exec-true-935adb3663.png) · [SVG](plots/coupled-population-concurrency-phase1-service-exec-true-exec-true-935adb3663.svg)
- coupled-population-concurrency: phase1-service / file_read_1k / file_read_1k: [PNG](plots/coupled-population-concurrency-phase1-service-file-read-1k-file-read-1k-6abcb3c08c.png) · [SVG](plots/coupled-population-concurrency-phase1-service-file-read-1k-file-read-1k-6abcb3c08c.svg)
- coupled-population-concurrency: phase1-service / file_read_1m / file_read_1m: [PNG](plots/coupled-population-concurrency-phase1-service-file-read-1m-file-read-1m-cf853ef1bd.png) · [SVG](plots/coupled-population-concurrency-phase1-service-file-read-1m-file-read-1m-cf853ef1bd.svg)
- coupled-population-concurrency: phase1-service / file_write_1k / file_write_1k: [PNG](plots/coupled-population-concurrency-phase1-service-file-write-1k-file-write-1k-03aef820f0.png) · [SVG](plots/coupled-population-concurrency-phase1-service-file-write-1k-file-write-1k-03aef820f0.svg)

No cross-phase figure was produced because this input did not contain an eligible matched pair. No missing comparison results were inferred.
