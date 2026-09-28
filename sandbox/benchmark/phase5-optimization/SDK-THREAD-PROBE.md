# E2B transport thread-pool probe

Six isolated CPU processes verified that **`TOKIO_WORKER_THREADS=1` reduces the E2B 2.50.0 transport's initial Tokio worker pool from 192 threads to one** on this container's 192-CPU affinity. All six local HTTP requests succeeded. The setting was applied only to these probe processes; the active training environment and frozen runtime code were unchanged.

| Measurement | Default, 3 repeats | `TOKIO_WORKER_THREADS=1`, 3 repeats |
|---|---:|---:|
| Tokio-named threads after SDK import | 0 | 0 |
| Tokio-named threads after transport factory | 192 | 1 |
| Total process threads after transport factory | 194 | 3 |
| Tokio-named threads after first local GET | 193 | 2 |
| Total process threads after first local GET | 195 | 4 |
| RSS after transport factory, median | 54 MiB | 51 MiB |
| Transport factory duration, median | 35.56 ms | 29.74 ms |
| SDK import + factory + first local GET, median | 248.31 ms | 242.44 ms |

Thread counts and the reported RSS values were identical across the three repeats in each condition. Factory durations ranged from 35.05–36.39 ms by default and 29.54–30.12 ms with one worker. After HTTPX client close, the observed thread counts remained unchanged because E2B retains the shared transport and its process-global Tokio runtime.

An additional Tokio-named thread appeared after the first request in both conditions. The environment variable controls async runtime workers; it does not cap all Tokio-named threads, the separate blocking pool, or Python/Ray threads. The role of that extra observed thread was not traced.

This is **not evidence of RL throughput improvement or an E2B server root cause**. The measured import/factory/local-request difference is about 6 ms. The roughly 3 MiB RSS difference belongs to these small probe processes and cannot be applied directly to the approximately 2.1 GiB RSS of production Ray workers. RSS includes shared mappings and is not unique physical memory. Reserved virtual address space also decreased, but that is not reclaimed resident RAM.

## Method and provenance

The test called the actual pinned SDK factory:

```python
from e2b.api.client_async import get_httpx_transport
transport = get_httpx_transport(proxy=None)
```

It sent one GET through `httpx.AsyncClient` to an in-process HTTP server bound to `127.0.0.1` on an ephemeral port. No E2B API, credential file, sandbox creation, Ray, torch, or GPU operation was used. Each worker asserted that no torch/vLLM/Ray module had been imported and that the server received exactly one expected loopback request. Proxy environment settings were cleared only inside the probe child. An independent 25-second process timeout bounded every worker.

Conditions were interleaved as default/one, one/default, default/one. Measurements were taken before SDK import, after import, after factory construction, after the first GET, and after HTTPX client close. The reported startup sum excludes deliberate settling delays and `/proc` measurement overhead. Three repeats and one local request per process are insufficient to assess sustained concurrency, streaming RPCs, or real agent behavior.

Versions: E2B **2.50.0**, pyqwest **0.10.0**, HTTPX **0.28.1**. The installed native pyqwest extension matches the published wheel bytes. Its source lock pins pyo3-async-runtimes 0.29.0 and Tokio 1.52.3. The builder uses a process-global runtime without an explicit worker-count override, so Tokio reads `TOKIO_WORKER_THREADS` before initialization. A running runtime cannot be resized by changing the variable afterward. Changing this setting for training requires new processes and an explicitly reviewed continuation or comparison.

- [Probe script](code/sdk_thread_probe.py).
- [Six-run summary](/data2/yuhanya/e2b-rl-pathfinding-20260920/results/sdk-thread-probe/20260920T172744Z/summary.json).
- [Protocol, individual observations and stderr records](/data2/yuhanya/e2b-rl-pathfinding-20260920/results/sdk-thread-probe/20260920T172744Z).
- [Versioned source evidence](/data2/yuhanya/e2b-rl-pathfinding-20260920/results/sdk-thread-probe/SOURCE-EVIDENCE.md), including archive checksums, exact source lines and installed binary identity.

Reproduce with the dedicated container available:

```bash
python3 /home/yuhanya/Daily_LLM/sandbox/benchmark/phase5-optimization/code/sdk_thread_probe.py
```
