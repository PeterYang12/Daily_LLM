# Rollout serving benchmark on one MI355X node: tensor-parallel size and prefill-decode disaggregation

2026-11-23. Model: Qwen3-Coder-30B-A3B-Instruct. Hardware: one node, 8 × AMD Instinct MI355X.

## Setup

| Item | Value |
|---|---|
| Image | `yangyuhanintel/verl-qat:primus-v26.9` (ROCm 10.2, PyTorch 2.16, vLLM 0.32.0 built from source) |
| Serving | plain `vllm serve`, one process per engine; no verl, no sandboxes |
| Engine flags | `--max-model-len 131072 --max-num-batched-tokens 131072 --gpu-memory-utilization 0.8 --enable-prefix-caching --enable-chunked-prefill --attention-backend ROCM_AITER_UNIFIED_ATTN --max-num-seqs 1024`, `VLLM_ROCM_USE_AITER=0` |
| PD transport | vLLM `MoRIIOConnector`, xGMI backend, READ mode unless noted; `amd_mori` 1.2.5 installed in the container (not in the image) |
| Workload | replay of recorded uni-agent SWE sessions (multi-turn): about 42 turns per session, request input 20K tokens at the median (39K at p90), output 72 tokens at the median, session length about 36K tokens at the end |
| Load | N sessions in flight at all times (N = 128, 256, 512), drawn from a pool of 2N sessions; 1 s pause between turns stands in for the tool call; each session stays on one engine |

Every configuration started with fresh engines. Each row is one run.

## Metrics

| Column | Meaning |
|---|---|
| Throughput | output tokens per second for the whole node while N sessions are in flight |
| TTFT | time to first token of a turn, seconds |
| TPOT | time per output token of a turn, milliseconds |
| Hit rate | prefix-cache hit rate reported by the engines at the end of the run, averaged over engines |
| KV in use | share of an engine's KV cache held by requests being processed (median / peak over the run). It does not count cached blocks kept for reuse, so a low value does not mean the cache is empty |

For PD rows, hit rate and KV in use are given as prefill engines / decode engines.

## SWE replay: results by concurrency (8 GPUs)

The PD row in these tables is the 1:1 layout at TP2 (2 prefill + 2 decode engines, 2 GPUs each). Other PD layouts
are in the next section.

### 128 sessions in flight

| Configuration | Throughput (tok/s) | TTFT p50 / p90 (s) | TPOT p50 / p90 (ms) | Hit rate | KV in use, median / peak |
|---|---|---|---|---|---|
| Non-PD, 8 engines × TP1 | 5708 | 0.08 / 0.15 | 17.3 / 20.0 | 95.3% | 19% / 30% |
| Non-PD, 4 engines × TP2 | 5219 | 0.08 / 0.26 | 19.4 / 22.0 | 96.2% | 16% / 21% |
| Non-PD, 2 engines × TP4 | 5240 | 0.07 / 0.58 | 19.2 / 21.4 | 96.2% | 15% / 18% |
| PD, 2 prefill + 2 decode, TP2 | 3758 | 0.17 / 0.48 | 28.7 / 32.4 | 96.3% / 96.3% | 1% / 5% and 33% / 43% |

### 256 sessions in flight

| Configuration | Throughput (tok/s) | TTFT p50 / p90 (s) | TPOT p50 / p90 (ms) | Hit rate | KV in use, median / peak |
|---|---|---|---|---|---|
| Non-PD, 8 engines × TP1 | 7928 | 0.10 / 0.19 | 26.6 / 31.4 | 96.2% | 38% / 50% |
| Non-PD, 4 engines × TP2 | 7427 | 0.10 / 0.31 | 29.1 / 32.8 | 96.1% | 32% / 41% |
| Non-PD, 2 engines × TP4 | 7572 | 0.10 / 0.51 | 28.3 / 31.6 | 96.2% | 30% / 34% |
| PD, 2 prefill + 2 decode, TP2 | 4946 | 0.23 / 0.75 | 45.2 / 55.2 | 95.9% / 96.2% | 1% / 16% and 67% / 80% |

### 512 sessions in flight

| Configuration | Throughput (tok/s) | TTFT p50 / p90 (s) | TPOT p50 / p90 (ms) | Hit rate | KV in use, median / peak |
|---|---|---|---|---|---|
| Non-PD, 8 engines × TP1 | 9698 | 0.16 / 0.48 | 44.8 / 55.9 | 95.0% | 79% / 100% |
| Non-PD, 4 engines × TP2 | 9266 | 0.17 / 0.52 | 48.4 / 57.3 | 96.1% | 68% / 80% |
| Non-PD, 2 engines × TP4 | 8754 | 0.19 / 0.60 | 51.2 / 58.8 | 95.9% | 63% / 70% |
| PD, 2 prefill + 2 decode, TP2 | 3622 | 8.97 / 14.92 | 87.5 / 101.4 | 43.2% / 8.2% | 30% / 58% and 99% / 100% |

No request waited in an engine queue in any non-PD run. In the PD run at 512, each decode engine had 34 requests
waiting at the median.

## SWE replay: PD layouts (8 GPUs)

All layouts use 8 GPUs. "xPyD" is x prefill engines + y decode engines. Prefill and decode engines must use the
same TP for this model (see Notes).

| Sessions | Layout | TP | Mode | Throughput (tok/s) | TTFT p50 / p90 (s) | TPOT p50 / p90 (ms) | Prefill hit rate | Requests waiting per prefill engine | Run |
|---|---|---|---|---|---|---|---|---|---|
| 128 | 2P2D | 2 | READ | 3758 | 0.17 / 0.48 | 28.7 / 32.4 | 96.3% | 0 | complete |
| 128 | 1P3D | 2 | READ | 4451 | 0.20 / 1.17 | 22.4 / 27.0 | 96.8% | 0 | complete |
| 128 | 4P4D | 1 | READ | 3811 | 0.17 / 0.29 | 28.2 / 32.8 | 96.3% | 0 | complete |
| 128 | 2P6D | 1 | READ | 4626 | 0.19 / 0.64 | 21.8 / 26.7 | 94.0% | 0 | complete |
| 128 | 1P7D | 1 | WRITE | ~915 | – | – | 12.0% | 29 | stopped after 10 min |
| 256 | 2P2D | 2 | READ | 4946 | 0.23 / 0.75 | 45.2 / 55.2 | 95.9% | 0 | complete |
| 256 | 1P3D | 2 | READ | ~1110 | – | – | 8.2% | 232 | stopped after 26 min |
| 256 | 4P4D | 1 | READ | 4862 | 0.23 / 0.81 | 46.6 / 55.9 | 94.3% | 0 | complete |
| 256 | 2P6D | 1 | READ | ~1490 | – | – | 7.9% | 104 | stopped after 28 min |
| 256 | 1P7D | 1 | WRITE | ~1250 | – | – | 12.5% | 214 | stopped after 10 min |
| 512 | 2P2D | 2 | READ | 3622 | 8.97 / 14.92 | 87.5 / 101.4 | 43.2% | 0 | complete |
| 512 | 1P3D | 2 | READ | ~1140 | – | – | 11.1% | 474 | stopped after 26 min |
| 512 | 4P4D | 1 | READ | 3699 | 9.44 / 56.55 | 51.9 / 79.7 | 27.0% | 78 | complete |

Rows marked "stopped" were ended by hand once the prefill engines were clearly overloaded. Their throughput (~) is
the mean of the engines' own "generation throughput" log lines, not the client measurement, and they have no TTFT /
TPOT. On completed runs the two methods agree within 1–6%.

Best PD layout against the best non-PD configuration (8 × TP1):

| Sessions | Non-PD 8 × TP1 | Best PD layout | PD relative to non-PD |
|---|---|---|---|
| 128 | 5708 | 4626 (2P6D, TP1) | −19% |
| 256 | 7928 | 4946 (2P2D, TP2) | −38% |
| 512 | 9698 | 3699 (4P4D, TP1) | −62% |

## SWE replay: 2-GPU and 4-GPU runs

Same workload and engine flags as above, all engines TP1. Several of these stacks ran side by side on one node,
each on its own GPUs.

PD (1 prefill + 1 decode engine) against 2 plain engines, 2 GPUs each:

| Sessions | Configuration | Throughput (tok/s) | TTFT p50 / p90 (s) | TPOT p50 / p90 (ms) | Hit rate | KV in use, median / peak | Requests waiting per engine |
|---|---|---|---|---|---|---|---|
| 64 | Non-PD, 2 engines × TP1 | 1831 | 0.11 / 0.21 | 29.4 / 33.7 | 96.0% | 41% / 49% | 0 |
| 64 | PD, 1P1D, TP1 | 1129 | 0.24 / 0.80 | 51.3 / 59.3 | 93.9% / 86.6% | 3% / 13% and 84% / 99% | 0 / 0 |
| 128 | Non-PD, 2 engines × TP1 | 2110 | 0.16 / 0.64 | 48.8 / 57.4 | 80.2% | 86% / 100% | 0 |
| 128 | PD, 1P1D, TP1 | 778 | 36.55 / 57.75 | 22.5 / 73.5 | 7.6% / 7.5% | 33% / 92% and 22% / 100% | 96 / 0 |

In the PD run at 128 sessions the requests queue at the prefill engine, so the decode engine has only about 13
requests in progress; that is why its TPOT median is low while TTFT is 37 s.

PD with 1 prefill + 3 decode engines on 4 GPUs (no non-PD run at the same size):

| Sessions | Configuration | Throughput (tok/s) | TTFT p50 / p90 (s) | TPOT p50 / p90 (ms) | Hit rate | KV in use, median / peak | Requests waiting per engine |
|---|---|---|---|---|---|---|---|
| 32 | PD, 1P3D, TP1 | 1716 | 0.14 / 0.24 | 13.6 / 16.1 | 95.7% / 95.0% | 3% / 8% and 11% / 20% | 0 / 0 |

## SWE replay: other runs

| Run | Result |
|---|---|
| Non-PD on 4 GPUs (4 × TP1), 512 sessions | ~1870 tok/s, hit rate 7.2%, KV in use 99%, 56 requests waiting per engine; stopped after 64 min, number from engine logs |
| PD with prefill TP2 × 1 + decode TP1 × 6 | not possible: the decode engines exit on the first PD request with `NotImplementedError: MoRIIO heterogeneous TP requires replicated KV heads on both prefill and decode. Got total_num_kv_heads=4, local_tp_size=1, remote_tp_size=2.` |

## AgentX

[AgentX](https://inferencex.semianalysis.com/infx/agentx) (agentx-harness 1.0.14) replays recorded Claude Code
sessions against one OpenAI-compatible URL. Differences from the SWE replay setup:

| Item | Value |
|---|---|
| Dataset | `semianalysis_cc_traces_weka_062126_256k` (393 sessions, contexts capped at 256K tokens) |
| Requests | multi-turn with prefix reuse; input 57K–75K tokens at the median in these runs, output 210–250 tokens at the median |
| Engine flags | as above, with `--max-model-len 262144 --enable-prompt-tokens-details --enable-auto-tool-choice --tool-call-parser qwen3_coder` |
| Routing | a session-sticky router in front of the engines (`X-Session-Id` from the harness); for PD the same router sends the prefill and decode legs |
| Run | warm-up (about 11 requests per session lane), then a 900 s measured window (the shortest the `agentx` scenario accepts) |
| Metrics | reported by the harness; TPOT is its inter-token latency. "Input read from cache" is the share of prompt tokens the server reported as cached; hit rate and KV in use come from the engine logs as before |

Several stacks ran side by side on one node, each on its own GPUs. No run below had request errors. For PD rows,
hit rate, KV in use and waiting requests are given as prefill engines / decode engines; "input read from cache" is
not given for PD because the decode engine reports every prompt token as cached (it receives the KV from the
prefill engine).

TP2 engines, 4 GPUs per stack, 32 sessions in flight:

| Configuration | Throughput (tok/s) | Requests in 900 s | TTFT p50 / p90 (s) | TPOT p50 / p90 (ms) | Input read from cache | Hit rate | KV in use, median / peak | Requests waiting per engine (mean) |
|---|---|---|---|---|---|---|---|---|
| Non-PD, 2 engines × TP2 | 738 | 1098 | 0.36 / 1.09 | 32.0 / 44.3 | 94.7% | 92.3%, 90.8% | 37% / 66%, 22% / 49% | 0.3, 0.2 |
| PD, 1P1D, TP2 | 472 | 832 | 1.31 / 14.0 | 53.6 / 65.1 | – | 89.4% / 71.3% | 10% / 94% and 17% / 100% | 6.1 / 0.6 |

TP1 engines, 2 or 4 GPUs per stack:

| GPUs | Sessions | Configuration | Throughput (tok/s) | Requests in 900 s | TTFT p50 / p90 (s) | TPOT p50 / p90 (ms) | Input read from cache | Hit rate | KV in use, median / peak | Requests waiting per engine (mean) |
|---|---|---|---|---|---|---|---|---|---|---|
| 2 | 32 | Non-PD, 2 engines × TP1 | 239 | 490 | 1.02 / 40.9 | 49.4 / 149.2 | 56.9% | 43.7%, 50.5% | 18% / 100%, 15% / 100% | 6.7, 4.7 |
| 2 | 32 | PD, 1P1D | 231 | 352 | 2.56 / 169.8 | 17.3 / 26.2 | – | 21.4% / 21.7% | 36% / 58% and 0% / 47% | 22.8 / 0.0 |
| 2 | 64 | Non-PD, 2 engines × TP1 | 250 | 358 | 1.00 / 271.4 | 41.1 / 146.7 | 53.2% | 17.1%, 11.3% | 14% / 100%, 14% / 100% | 22.0, 22.5 |
| 4 | 32 | PD, 1P3D (node 211) | 257 | 362 | 1.58 / 182.4 | 7.3 / 14.3 | – | 23.6% / 86% | 36% / 63% and 0% / 34% | 23.1 / 0.0 |
| 4 | 32 | PD, 1P3D (node 212, repeat) | 259 | 366 | 1.72 / 188.2 | 7.2 / 14.9 | – | 23.8% / 82% | 36% / 66% and 0% / 38% | 23.0 / 0.0 |
| 4 | 32 | PD, 2P2D | 447 | 763 | 1.94 / 24.8 | 40.3 / 53.7 | – | 36.5%, 78.2% / 24.9%, 49.6% | 35% / 81%, 10% / 90% and 0% / 100%, 23% / 100% | 10.1, 2.2 / 0.1, 0.5 |

A TP1 engine holds 1.66M tokens of KV. At 32 sessions of 60K–100K tokens each, every TP1 stack in this table is
short of KV memory: with 2 plain engines only 57% of the input is read from cache (the harness puts the achievable
share at 93%), and in the PD stacks the prefill engines' hit rate is 21–78% with requests queueing there. The 1P3D
stack keeps all 32 sessions on one prefill engine and its three decode engines are almost idle (under one request
in progress each); 2P2D spreads the sessions over two prefill engines and reaches 1.7 times the throughput of 1P3D.
There is no non-PD run on 4 GPUs at TP1 to compare these two with.

Runs that produced no result:

| Run | What happened |
|---|---|
| 8 × TP1 non-PD, PD 4P4D TP1, 4 × TP1 non-PD, 256 sessions | stopped after 33 min, still in warm-up; prefix hit rate 1–2%, 23–66 requests waiting per engine |
| 4-GPU PD 1P1D TP2 against 2 engines × TP2, 64 / 128 / 256 sessions | stopped after 30 min, still in warm-up; the PD run at 256 sessions aborted by itself on a dropped connection with about 190 requests waiting at the prefill engine |
| 2-GPU PD 1P1D TP1, 64 sessions | stopped after 93 min, still in warm-up (677 of 702 warm-up requests returned) |

## Observations

1. Without PD, TP1 gives the highest throughput at all three load levels (5–11% above TP2 and TP4).
2. No PD layout reached the non-PD throughput on the same 8 GPUs, and the gap grows with load.
3. A PD layout fails in one of two ways as load grows. With few prefill engines (1P3D, 2P6D, 1P7D), the prefill
   side cannot keep every session's context cached; its hit rate drops to about 10% and requests queue there while
   the decode engines idle. With many prefill engines (2P2D, 4P4D), the prefill engines are almost idle and the
   decode side runs out of capacity first.
4. In this workload about 96% of each request's input is served from the prefix cache, so there is little prefill
   work to move away. At the same number of sessions per engine, a PD decode engine and a plain engine generate at
   about the same speed (differences between −5% and +6%).
5. PD on 8 GPUs (4P4D, 3699 tok/s) is faster than non-PD on 4 GPUs (~1870 tok/s) at 512 sessions, and slower than
   non-PD on 8 GPUs (9698 tok/s).
6. The 2-GPU runs show the same picture at small scale: PD 1P1D is 38% below two plain engines at 64 sessions and
   63% below at 128 sessions, where the single prefill engine can no longer keep the sessions cached.
7. AgentX behaves like the SWE replay when KV memory is sufficient: about 95% of the input is read from the prefix
   cache, and PD 1P1D is 36% below two plain engines on the same 4 GPUs (TP2) at 32 sessions, with higher TTFT and
   TPOT.
8. On 2 GPUs at TP1, where both stacks are short of KV memory, AgentX throughput is about equal (PD 231 against
   non-PD 239 tok/s at 32 sessions). PD has the lower TPOT (17 against 49 ms) and the much higher TTFT (p90 170 s
   against 41 s).

## Notes and limits

- One model, one workload, one PD implementation (vLLM + MoRI-IO). AITER kernels were off (`VLLM_ROCM_USE_AITER=0`).
- The replay forces the recorded output length, so it measures speed only. Each PD run was preceded by a greedy
  comparison of PD output against direct inference on 20K-token contexts.
- Throughput is measured without a latency target. PD is usually evaluated as throughput at a required TTFT / TPOT,
  which these runs do not cover.
- In the 2P2D runs at 128 and 256 sessions, 1.4–1.5% of the requests had the connection between router and client
  reset while the response was being returned; the client retried them and the final error count was 0.
- AgentX results exist for 32 sessions (plus one non-PD run at 64); higher session counts overloaded the small
  stacks and did not get past warm-up. The AgentX runs used the 900 s minimum window instead of the default one hour.
- In the AgentX runs the router logged a few connection resets while returning responses (2–28 per run); the harness
  retried them and reported no request errors.

## Reproduce

Tools and raw results are on the shared volume, not in this repository:

| What | Where |
|---|---|
| Engine launcher, PD router, replay client, queue runner | `/shared_nfs/yuhan/pd_mori_logs/replay/` (`serve2.sh`, `pd_proxy2.py`, `replay2.py`, `bench_node2.sh`, `README.md`) |
| Result tables (`table.py`, `tpot.py`) and per-run logs | `/shared_nfs/yuhan/pd_mori_logs/replay/results2/` (`TABLE_all_1123.txt`) |
| AgentX router, launch scripts and run outputs | `/shared_nfs/yuhan/pd_mori_logs/agentx/` (`router.py`, `serve_split.sh`, `serve_pairs.sh`, `run_agentx.sh`, `runs/`) |
| Session recordings | `/shared_nfs/yuhan/runtime/logs/Uni-Agent-Qwen3-Coder-30B-react-e2b/1022_crusoe_react_e2b_sepasync_16r8t/step_40`, `step_41` |

Queue line format for `bench_node2.sh`, one run per line in `queue2_<node>.txt`:

```
nonpd_tp1_unified_c256 nonpd 1 EXTRA=--attention-backend+ROCM_AITER_UNIFIED_ATTN+--max-num-seqs+1024 CONC=256 NEP=512 BATCHES=40
pd_tp2_2p2d_read_c256  pd 2 2 2 2 read EXTRA=--max-num-seqs+1024 CONC=256 NEP=512 BATCHES=40
```

`nonpd <tp> [n_gpus]`; `pd <tp_prefill> <n_prefill> <tp_decode> <n_decode> <read|write>`; `CONC` is sessions in
flight, `NEP` the session pool.
