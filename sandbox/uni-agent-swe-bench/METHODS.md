# Measurement methods and reproducibility

The source runtime is identified in `data/summary.json`. Extraction selects only the 256 result files whose `started_unix` is at or after attempt 3's launch. Each session is joined to exactly one created event, one cached-tool provisioning event, one destroyed event, a Task log and a framework/trajectory record. All groups must have eight distinct rollout indices and each cohort must have sixteen tasks.

## Boundaries

- `episode_s`: precise Task runner entry-to-return interval from its result record. Framework dispatch/import before entry is separate.
- `create_wait_and_api_s`: the provider's monotonic timer begins before the Ray create semaphore. It includes admission queueing and the API call; it ends when create returns. It is not pure server boot time.
- `post_create_setup_s`: create acknowledgement to Agent STEP 1. Includes readiness, cached tools, repository cleanup and persistent-shell setup.
- `tools_provision_s`: timed cached archive upload/install/check; nested inside post-create setup and inside the create permit's lifetime.
- `agent_s`: STEP 1 marker to Episode done marker. Model-query and logical-tool intervals are parsed only from anchored `agents.react.agent` logger lines, not from arbitrary test output that may contain the words ERROR or retry.
- `model_roundtrip_s`: STEP marker to corresponding Prompt Tokens marker. Includes preparation, gateway/service queueing, inference, transport and any client retry delay. Endpoints are rounded to seconds. It is not pure decode time.
- `logical_tools_s`: ACTION to OBSERVATION/TIMEOUT/TOOL ERROR. Includes control RPCs, gate waits, process execution, polling and capture. An interval rounded to zero does not mean no cost.
- `verifier_s`: the explicit `eval_execution_time` timer. The final instrumented exec per episode is checked to coincide with the evaluator. Its own timer starts after the exec gate, so it is nested inside the verifier timer.
- `destroy_s`: client-visible successful kill acknowledgement interval. Server-side physical teardown is not instrumented.
- `other_episode_s`: runner duration minus the main measured/reconstructed phases. It includes small local gaps and timestamp resolution residue, not a separately traced subsystem.

## Parallel-time rules

Trainer step-1 timers are authoritative wall-clock values. Cohort windows use the earliest framework log timestamp and the latest framework/precise runner end; the boundary has roughly one-second resolution. Episode and live-sandbox durations are summed only when explicitly labeled cumulative. Percentiles of durations use NumPy linear quantiles. Completion milestones use nearest rank: ceil(fraction × 128).

Concurrency is reconstructed by sweeping acknowledged create/destroy intervals; API-sampled counts use the recorded ten-second monitor samples. The former can detect a brief peak missed by sampling, but it still describes client-acknowledged lifecycle, not physical CPU allocation. GPU busy is integrated as a left-held sampled counter over the cohort window. It is neither MFU nor a direct throughput measure. No sampling gap exceeded 30 seconds in the analyzed cohort windows.

The post-create gate occupancy lower bound intersects each [create-return, tool-provision-end] interval with the creation ramp and divides summed overlap by 3 × ramp duration. Source code holds the permit throughout each such interval. Unmeasured API time and release overhead are omitted, so this is a lower bound without assuming FIFO request ordering.

## Integrity checks

Extraction asserts 256 complete episodes, 32 groups of eight, matching tool/action counts, matching Agent steps/model-query result counts, matching timeout/error counts, and a valid final verifier event. Model-generated token counts summed from query usage equal exported model-token masks for all 256 episodes. The prior runtime audit checked token/mask/logprob lengths, finite logprobs and Task reward agreement. Structured phase residuals are under two seconds per episode.

Framework timestamps are integer seconds. `framework_finalize_log_delta_s` can be negative from rounding; its lower/upper fields bound the plausible nonnegative interval. It must not be averaged as a negative processing cost. Dispatch log deltas are upper bounds with less than one second of rounding uncertainty.

The reported trainer `clip_ratio` and `num_turns` definitions were checked in the pinned source. The former compares against padded batch maxima; the latter counts user/assistant messages plus one. They are not silently equated to budget exhaustion or Agent loop iterations.

## Limits

No server-side E2B boot spans, whole-node CPU/RAM/disk metrics, per-request inference queue/prefill/decode splits, cache-hit counters, kernel timeline or checkpoint I/O timing were recorded. We cannot quantify a native-shell speedup, the benefit of larger concurrency, or the effect of a different n/batch configuration from this run. Shared resources, different tasks and different model versions between cohorts prevent treating the two batches as a controlled A/B test.

## Rebuild

Run `python3 analysis/extract.py --runtime /path/to/the/original/run`, then `python3 analysis/render.py`, then `python3 analysis/write_report.py`. Packages: numpy, pandas, matplotlib and markdown. Extraction requires the original run. Figure generation uses included evidence when present and otherwise falls back to the original runtime; report generation refreshes evidence copies from that runtime. `data/source-manifest.json` records absolute original input paths and SHA-256 hashes; `data/evidence-manifest.json` also covers the copied source/configuration evidence. The generated CSVs and timing markers preserve derived evidence without duplicating full model thoughts and tool outputs. The offline HTML explorer embeds only selected timing/result columns. Run `python3 analysis/validate.py` after generation to verify accounting, joins, source/evidence hashes and document links; results are written to `data/validation.json`.
