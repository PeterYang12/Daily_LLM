# PD against plain engines: DeepSeek-V4-Flash-0731, TP4, AgentX

2026-11-23. One node, 8 × MI355X, vLLM 0.32.1.dev (image `yangyuhanintel/verl-qat:primus-v26.9`), aiter on
(`VLLM_ROCM_USE_AITER=1`). All engines TP4.

| Configuration | GPUs 0–3 | GPUs 4–7 |
|---|---|---|
| Plain | engine, TP4 | engine, TP4 |
| PD 1P1D | prefill engine, TP4 | decode engine, TP4 |

PD uses MoRI-IO (xGMI) in READ mode. Workload: AgentX (`agentx-harness` 1.0.14, dataset
`semianalysis_cc_traces_weka_062126_256k`), 900 s measured window, fresh engines for every run. A session-sticky
router sits in front of the engines in both configurations.

## Conclusion

PD 1P1D was not faster than two plain engines at any concurrency: throughput was 3% to 17% lower and time to first
token was 2 to 50 times longer. PD did give steadier token latency: p99 inter-token latency was 39% to 64% lower.
Above 256 sessions the single prefill engine could not keep up at all.

## Results

Columns:

- **Hit rate**: prefix-cache hit rate reported by the engines. The trace allows 93–95% at most.
- **KV in use**: share of an engine's KV cache held by requests being processed, median / peak. Cached blocks kept
  for reuse are not counted. One TP4 engine holds 4.08M tokens.
- **Waiting**: mean number of requests queued in an engine.

Plain rows give engine 0, engine 1. PD rows give prefill engine / decode engine. The decode engine's hit rate is
left out: in READ mode it takes all KV from the prefill engine.

| Sessions | Configuration | Throughput (tok/s) | TTFT p50 / p90 (s) | Inter-token latency p50 / p99 (ms) | Hit rate | KV in use, median / peak | Waiting |
|---|---|---|---|---|---|---|---|
| 64 | Plain | 1695 | 0.36 / 0.92 | 20.7 / 51.5 | 94.5%, 95.2% | 4% / 12%, 6% / 12% | 3.6, 3.2 |
| 64 | PD 1P1D | 1535 (−9%) | 0.82 / 2.63 | 23.0 / 31.5 | 95.5% | 0% / 13% and 3% / 14% | 12.0 / 0 |
| 128 | Plain | 3162 | 0.40 / 1.48 | 33.4 / 74.2 | 93.6%, 92.9% | 7% / 20%, 6% / 20% | 12.5, 11.6 |
| 128 | PD 1P1D | 3068 (−3%) | 1.62 / 7.44 | 34.9 / 44.8 | 93.1% | 6% / 15% and 3% / 28% | 41.5 / 0 |
| 256 | Plain | 3648 | 0.55 / 2.81 | 53.1 / 143.8 | 93.7%, 94.1% | 6% / 38%, 6% / 33% | 32.3, 42.7 |
| 256 | PD 1P1D | 3027 (−17%) | 27.2 / 63.8 | 36.1 / 52.4 | 92.1% | 6% / 17% and 0% / 38% | 147.7 / 0 |
| 512 | Plain | 2664 | 1.66 / 20.1 | 154.9 / 458.2 | 86.7%, 86.2% | 6% / 71%, 6% / 72% | 121.6, 120.0 |
| 512 | PD 1P1D | no result (warm-up only) | – | – | 8.1% | 6% / 7% and 0% / 0% | about 570 / 0 |
| 1024 | Plain | no result (warm-up only) | – | – | 6.0%, 6.2% | 6% / 6%, 6% / 6% | about 570 each |
| 1024 | PD 1P1D | no result (warm-up only) | – | – | 21.3% | 6% / 7% and 0% / 0% | about 1,250 / 0 |

No errors were reported by AgentX in the completed runs.

Runs without a result did not get through AgentX's warm-up within the 75-minute limit I set per run:

| Run | Warm-up requests returned after 75 min | Requests waiting at the end |
|---|---|---|
| PD 1P1D, 512 | 1,901 of 5,608 | about 570 on the prefill engine |
| PD 1P1D, 1024 | 2,250 of 11,190 | about 1,250 on the prefill engine |
| Plain, 1024 | 3,747 of 11,190 | about 570 on each engine |

Plain at 512 finished its warm-up in about 29 minutes. Plain throughput peaks near 256 sessions and falls at 512, so
512 and 1024 are beyond what one node serves for this model and workload.

## What the numbers say

1. **Throughput.** Plain is ahead at every load. The gap is smallest at 128 sessions (−3%) and grows to −17% at 256,
   where the prefill engine has 148 requests waiting on average.
2. **Time to first token.** PD is worse everywhere, and far worse at 256 (27 s against 0.55 s at the median). Every
   request has to pass through the one prefill engine, which runs 1 to 2 requests at a time.
3. **Inter-token latency.** This is where PD helps. The decode engine does no prefill, so its tail is tighter: p99
   is 31.5 ms against 51.5 ms at 64 sessions, 44.8 against 74.2 at 128, and 52.4 against 143.8 at 256. At 256 part
   of this comes from the decode engine simply carrying fewer requests (44 running, against 54–62 per plain engine),
   because the rest are queued at prefill.
4. **Cold contexts are the limit.** A cold 60K–70K-token context is prefilled at about 35K tokens/s per engine. PD
   has one engine for this work, plain has two.

## Caveats

- The model: there is no "0713" release; I used `deepseek-ai/DeepSeek-V4-Flash-0731`.
- vLLM's `MoRIIOConnector` does not start with DeepSeek V4 as shipped. I changed two start-up checks in the
  containers (not in the image): the attention-backend name lookup, and the rule that all KV layers share one block
  byte length (V4 has two). After that, PD answered a question about a 29K-token context correctly, but its greedy
  output diverged from a plain engine's after about 30 tokens. This path has no upstream validation, so treat the PD
  numbers as indicative.
- Plain ran on node 105 and PD on node 026 (same hardware and vLLM build), one run per cell.
- Engines used vLLM defaults for scheduling (`max_num_batched_tokens` 16384). No tuning was tried for either side.

## Files

Scripts and raw results: `/shared_nfs/yuhan/pd_mori_logs/agentx/` (`serve_dsv4.sh`, `run_dsv4.sh`, `go_dsv4.sh`,
`patch_moriio_dsv4.py`, `check_dsv4.py`; runs under `runs/*_dsv4_*`; `python3 summary.py <run>` prints one run).
