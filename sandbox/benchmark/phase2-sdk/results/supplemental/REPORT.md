# Measured benchmark report

Recorded runs: **27**. Configuration/operation groups: **14**. Data-quality errors: **0**.

Attempts excluded from performance measurement: **0**. They remain in the run evidence and must be included when assessing setup reliability.

This report contains only recorded observations. It does not establish sustainable capacity or an RL speedup.

The 2026-09-20 thin service path uses HTTPX and the SDK path uses pyqwest. Cross-phase differences include client transport and generator costs. Diagonal C=N sweeps change concurrency and resident population together; separate fixed-resident anchors are needed to isolate their effects.

For create/delete churn, the raw resident_count is an unused CLI default and is preserved as provenance. Derived tables mark resident count not applicable, and lifecycle figures omit N. The configured concurrency counts create/delete cycle workers; throughput includes cycle pacing while create latency covers the create call.

Failed setup attempts with no measured operations stay in runs.csv/json with their stop reason and reported window preserved. Their window is not a performance denominator, and they do not produce zero-throughput capacity points. A completed retry contributes its own measured result; the failed attempt remains separate evidence.

- `runs.csv/json`: whole-run windows, record completeness, cleanup and stop outcomes.
- `per_run.csv/json`: operation statistics for each independent run and repeat.
- `summary.csv/json`: median of per-run statistics, run min/max and sample counts.
- `data_quality.json`: gaps and errors; failed runs remain in the data.

Throughput counts completions inside the explicit measurement window. Latency covers operations dispatched inside that window, including their drain completions. Mixed-script operation rates use the entire script window. Failure rates count all observed terminal non-success outcomes, including invalid results; rates are null when dispatch accounting is incomplete.

Final counters are checked against all measurement-labelled terminal records. Exact timestamp windows still govern performance cohorts. Labelled operations starting outside the window are counted separately in runs.csv/json, and any failures remain in errors.csv/json; timestamps are not padded or changed.

Successful-operation latency quantiles exclude failed operations and must be read with failure and completeness counts. P99 based on fewer than 1,000 successful operations per run is weak descriptive evidence. Run min/max bands show observed variation, not confidence intervals. Cross-run summaries do not silently omit runs with missing metric values.

errors.csv/json retains failed operation timing, error class and RPC code. Exception names and messages describe observed failures; a TimeoutException name alone does not establish an elapsed timeout budget or sandbox TTL expiry. File payload rates describe logical application bytes, not measured network bandwidth; compression and transport behavior require separate diagnosis.

Open-loop figures distinguish failures among dispatched operations from scheduled arrivals that were never dispatched. Zero service errors do not mean every scheduled arrival was served. The current lifecycle rate sweeps are finite batches whose elapsed window includes drain, not steady-state capacity certifications.

## Data quality

| Severity | Finding | Count |
|---|---|---:|
| warning | dispatch_outside_measurement | 1 |
| warning | limited_p99_samples | 33 |
| warning | non_operation_records_skipped | 1 |
| warning | operation_id_reused_across_windows | 9 |
| warning | resident_count_not_applicable | 12 |

## Figures

- concurrency: phase2-sdk / create_delete_churn / create: [PNG](plots/concurrency-phase2-sdk-create-delete-churn-create-8818f949d1.png) · [SVG](plots/concurrency-phase2-sdk-create-delete-churn-create-8818f949d1.svg)

No cross-phase figure was produced because this input did not contain an eligible matched pair. No missing comparison results were inferred.
