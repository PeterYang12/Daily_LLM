# Prefill-decode disaggregation for agentic rollouts on MI355X: summary

2026-11-23. Qwen3-Coder-30B-A3B on one node with 8 × MI355X, vLLM 0.32 with MoRI-IO for KV transfer.
Full data and method: [README.md](README.md).

## Conclusion

On multi-turn agentic workloads, prefill-decode (PD) disaggregation did not improve throughput, time to first
token (TTFT) or time per output token (TPOT) in any layout we tried. Plain engines at TP1 were best at every load
level. The reason is that these workloads have almost no prefill work to move away: about 96% of each request's
input is already in the prefix cache.

## What we measured

Two workloads, both multi-turn with a growing context:

| Workload | Request input (median) | Output (median) | Input served from prefix cache |
|---|---|---|---|
| SWE replay (recorded uni-agent sessions) | 20K tokens | 72 tokens | about 96% |
| AgentX (recorded Claude Code sessions, 256K variant) | 57K–75K tokens | 210–250 tokens | about 95% |

Each run keeps N sessions in flight and reports output tokens per second for the whole stack.

## PD against plain engines on the same GPUs

Columns: TTFT is time to first token, TPOT is time per output token. Hit rate is the engines' prefix-cache hit rate.
KV in use is the share of an engine's KV cache held by requests being processed (median / peak); cached blocks kept
for reuse are not counted. Waiting is the number of requests queued in an engine (median). For PD rows the last
three columns are given as prefill engines / decode engines.

SWE replay, 8 GPUs. The PD row is the best of five layouts at that load (2P2D and 1P3D at TP2; 4P4D, 2P6D and 1P7D
at TP1).

| Sessions | Configuration | Throughput (tok/s) | TTFT p50 / p90 (s) | TPOT p50 / p90 (ms) | Hit rate | KV in use, median / peak | Waiting |
|---|---|---|---|---|---|---|---|
| 128 | Plain, 8 × TP1 | 5708 | 0.08 / 0.15 | 17.3 / 20.0 | 95.3% | 19% / 30% | 0 |
| 128 | PD, 2P6D, TP1 | 4626 (−19%) | 0.19 / 0.64 | 21.8 / 26.7 | 94.0% / 96.3% | 3% / 15% and 26% / 40% | 0 / 0 |
| 256 | Plain, 8 × TP1 | 7928 | 0.10 / 0.19 | 26.6 / 31.4 | 96.2% | 38% / 50% | 0 |
| 256 | PD, 2P2D, TP2 | 4946 (−38%) | 0.23 / 0.75 | 45.2 / 55.2 | 95.9% / 96.2% | 1% / 16% and 67% / 80% | 0 / 0 |
| 512 | Plain, 8 × TP1 | 9698 | 0.16 / 0.48 | 44.8 / 55.9 | 95.0% | 79% / 100% | 0 |
| 512 | PD, 4P4D, TP1 | 3699 (−62%) | 9.44 / 56.55 | 51.9 / 79.7 | 27.0% / 7.6% | 35% / 92% and 44% / 100% | 78 / 6 |

AgentX, 4 GPUs at TP2, 32 sessions, 900 s measured window:

| Configuration | Throughput (tok/s) | Requests completed | TTFT p50 / p90 (s) | TPOT p50 / p90 (ms) | Hit rate | KV in use, median / peak | Waiting (mean) |
|---|---|---|---|---|---|---|---|
| Plain, 2 engines | 738 | 1098 | 0.36 / 1.09 | 32.0 / 44.3 | 92.3%, 90.8% | 37% / 66%, 22% / 49% | 0.3, 0.2 |
| PD, 1 prefill + 1 decode | 472 (−36%) | 832 | 1.31 / 14.0 | 53.6 / 65.1 | 89.4% / 71.3% | 10% / 94% and 17% / 100% | 6.1 / 0.6 |

## Why PD does not help here

1. **Prefill is a small share of the work.** In the SWE replay, the time before the first token (prefill, KV
   transfer and first token) is 4% of a request's time and generation is 96%. In AgentX it is 3% and 97%.
2. **Taking prefill away does not make generation faster.** With the same number of sessions per engine, a PD decode
   engine and a plain engine generate at about the same speed (−5% to +6%). Without PD, TPOT is already steady
   (17.3 ms at the median, 23.4 ms at p99 at 128 sessions), so there is no interference for PD to remove.
3. **The prefill GPUs are then pure cost.** Example, 8 GPUs as 2P2D (TP2) at 256 sessions: the prefill engines
   process 0.2–0.3 requests at a time; each decode engine carries about 118.
4. **The prefill side cannot be made small.** It has to keep every session's context cached. With too few prefill
   engines its cache hit rate collapses and requests queue there:

| Layout (8 GPUs, 256 sessions) | Throughput | Prefill cache hit rate | Requests waiting per prefill engine |
|---|---|---|---|
| 2P2D, TP2 (half the GPUs on prefill) | 4946 tok/s | 95.9% | 0 |
| 1P3D, TP2 (a quarter on prefill) | about 1110 tok/s | 8.2% | 232 |

5. **Prefill and decode cannot use different TP sizes** for this model with vLLM + MoRI-IO, so a large-memory
   prefill engine cannot be paired with small, fast decode engines.

One comparison does favour PD: 8 GPUs as PD (4P4D, 3699 tok/s) against 4 plain GPUs (about 1870 tok/s) at 512
sessions. Adding prefill GPUs to a fixed set of decode GPUs helps; using the same GPUs as plain engines helps more
(9698 tok/s).
