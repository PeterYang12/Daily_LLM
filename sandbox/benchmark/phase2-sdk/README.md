# Phase 2 — Python SDK and code-interpreter baseline

This phase preserves the official Python performance script's sequence: create a
sandbox, invoke `curl` through `commands.run` against port 49999, run the same
Python Hello World twice, and delete the sandbox. The reference script is pinned
to E2B commit `ccaf9fc0ffe6ac39c7ec786af7608ab1de19467b` in
`vendor/sdk/performance.py`. No import of this file is used: upstream executes
work at module import.

`code/official_sdk_baseline.py` is a local adapter. Its differences are explicit:
monotonic timers, result verification, bounded timeouts, disabled create retries,
JSONL records including failures, and cleanup on error. The four SDK-call timers
end when each call returns; `run_code` includes consumption of the response
stream. Setup, cleanup and correctness checks are not extra workload operations.
The final measurement window ends before final cleanup. Its wall duration still
contains cleanup between iterations, matching the serial official sequence;
operation latency distributions do not contain those cleanup gaps.

Run-level `repeat` identifies an independently repeated run; `iteration` names
the serial fresh-sandbox cycles inside that run. Each cycle contributes four
operation records. P99 from a default 20-cycle run is descriptive, not a stable
tail estimate. The root analysis combines repetitions while retaining run-level
throughput.

## Environment and source records

- Python: `/data2/yuhanya/e2b-benchmark/session-20260920/env/bin/python`.
- SDK versions: `e2b==2.49.1`, `e2b-code-interpreter==2.10.0`.
- Plot dependency: `matplotlib==3.10.8`.
- All resolved client packages: `configs/client-requirements.lock`.
- Upstream URLs and SHA-256 for each vendored source: `configs/upstream-sources.json`.

The benchmark reads the existing private client configuration. Neither keys nor
connection secrets are copied into this directory. It refuses to start while a
non-benchmark sandbox is active. Workloads are coordinated by the root runner to
avoid overlap with template builds or phase 1 measurements.

## Local template adaptation

`code/prepare_template.py` builds a new alias `benchmark-ci-minimal-20260920`
with 2 vCPU and 4096 MiB, using the current service's ordinary-page configuration.
The existing `base` template is the source; existing aliases and service settings
are not edited. The code-interpreter server source is unchanged from official
`e2b-dev/code-interpreter` commit
`f56a1edf750e20a96f847df57e0063eeeb5e13c3`. Both Python and JavaScript kernels are
installed because the upstream server creates both default contexts at startup.
The E2B JavaScript kernel fork is pinned to
`79cb7d56dcfe0df9ff55eff0ca4dc920a6dcc361`.

This is **not the full hosted `code-interpreter-v1` template**. It omits R, Java,
Bash kernels and the large scientific-package inventory. Jupyter and the server
share `/opt/benchmark-ci`; the systemd unit paths are adapted accordingly. It
retains the upstream Jupyter settings and IPython startup hooks. The actual
resolved Python/Node packages and kernel list are exported by the single setup
smoke sandbox to `results/template-smoke.json`.

The build SDK's file upload URLs point at the VM's loopback port 5008. During the
build only, a loopback SSH forwarding process maps host `127.0.0.1:5008` to the
same VM port. No service listener/configuration is changed. Template build logs
and identity are retained separately from measurement results.

## Entry points

`prepare_template.py` defaults to preparation-only; `--build` registers the new
template. It refuses to overwrite a prior build report, so an interrupted build
must be reconciled before another POST. `smoke_template.py` creates at most one
setup sandbox per invocation, checks Hello World twice, captures package/kernel
versions, verifies actual Firecracker 4 KiB RAM backing, and deletes the sandbox.

For the measured baseline, use `official_sdk_baseline.py --template ALIAS
--output NEW.jsonl --iterations 20 --repeat N`. The shared runner chooses timing
and repetition. Tests in `code/test_official_sdk_baseline.py` use an in-memory
fake and make no service calls; they check successful accounting and cleanup
after invalid execution output.

## Completed setup, 2026-09-20 UTC

- Build: 10:29:10–10:32:56, 225.38 seconds including upload/polling.
- Template ID: `96k1k8rxir5it2bs2b7r`.
- Build ID: `2e0140f3-113d-4e03-bb2b-b817a904f413`.
- The single setup sandbox ran 10:34:19–10:34:23 and was removed; verified
  owned instance count after cleanup is zero.
- Both Hello World executions returned correct stdout and execution counts 1/2.
- Actual Firecracker `huge_pages=None`, both guest RAM mappings have 4096-byte
  pages, and `smaps` reports 4 kB with no HugeTLB/THP usage.
- Guest runtime: Python 3.11.6, Node v20.9.0. The resolved package and kernel
  inventory is in `results/template-smoke.json`.
- The temporary upload tunnel was stopped; port 5008 is no longer listening on
  the outer host. No core service configuration changed.

The setup smoke timings are functional preparation evidence, not samples in the
measured benchmark distribution. Formal repetitions are scheduled independently
by the root runner after phase 1.
