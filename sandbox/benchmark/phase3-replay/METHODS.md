# Phase 3: recovered older 8B SWE-bench action corpus

The runnable private corpus is `/data2/yuhanya/e2b-rl-pathfinding-20260920/data/replay-corpus/older8b-v2/manifest.jsonl`. Its SHA-256 is `e02af08919fbda7cb718fe1cf7464290df3060f381b3df607339c1b60d36611b`. `older8b-v1` is preliminary extraction evidence; use v2 for execution. The audit-only command completed successfully. No sandbox replay was launched during corpus preparation.

This corpus comes from older Qwen3-8B SWE-bench runs. It is **not** the newer Qwen3-Coder-30B report's 32-task/256-session corpus, whose original runtime directory was unavailable. Source task logs, source hashes, private argument/observation blocks, original tool settings and current template/build mappings are preserved alongside each episode.

| Source | Logs scanned | Selected episodes | Historical sandbox provider |
|---|---:|---:|---|
| `swe8b300_20260914_a`, attempt-001 | 80 | 6 | Custom E2B provider |
| `uni-agent-lab/runs/rl/swe_separate_2` | 25 | 2 | Docker |
| Total | 105 | 8 | Replays target current local E2B |

Twenty-three complete candidates matched the current qualified Verified mapping. Selection first takes the shortest action sequence per source/task stratum, then the longest distinct sequence in each stratum, up to eight episodes. It does not filter by reward. The selected set includes two historically resolved episodes, unsuccessful attempts, and maximum-step loops.

| Task | Selected action counts | Notes |
|---|---|---|
| `pallets__flask-5014` | 15, 32, 2, 9 | Two historical E2B and two historical Docker episodes |
| `psf__requests-6028` | 14, 32 | Includes a long repeated-action case |
| `pydata__xarray-3095` | 7 | Historical validation episode |
| `sympy__sympy-20438` | 32 | Historical validation episode with repeated editor actions |

There are **143 actual observed actions: 94 editor calls and 49 shell calls**. All 143 action blocks have matching observations. Native episode-terminal counters agree for all eight episodes, including tool, timeout and error counts. All selected sources reached native task scoring. The corpus contains full multiline JSON arguments, not previews reconstructed from low-level RPC logs.

## Extraction and fidelity checks

`code/profile_extract_replay.py` parses anchored native log records. Only `agents.react.agent` ACTION/OBSERVATION/TIMEOUT/TOOL markers define actions. Each ACTION must contain complete JSON and match the next corresponding observed tool result. Missing or mismatched blocks, absent terminal markers, inconsistent native counters, mismatched trajectory rewards, and tasks outside the current mapping are recorded as exclusions. No payload is invented.

The exact original tool specifications are retained, including persistent-shell environment settings and command timeout. The selected historical configurations had `action_timeout: null`; the replay preserves that value and keeps the tools' own command timeouts. Integer-second log timestamps are retained as historical markers, not converted into precise model wait durations. The initial execution mode is ASAP: external model gaps are removed, while every command's own sleeps, tests and I/O remain intact.

Every original image name matches the corresponding current mapped image name. The current template and build IDs are recorded. Historical image digests, old provider versions and machine resources are not fully reconstructed, so image-name equality does not establish complete environment equality. In particular, the two Docker episodes are a cross-provider replay, not an E2B latency baseline.

All observations remain in private corpus evidence. Sixty-two deterministic editor-view observations receive normalized SHA-256 checks. Normalization removes only trailing line endings introduced by log framing. Arbitrary shell output may contain durations, temporary paths or process IDs; it is retained in full as evidence, while the executable checks its tool status. Optional file-state assertions can strengthen individual actions later. Status-only matches alone do not prove full semantic equivalence.

The runner stops an episode at its first status, observation-hash or declared file-state divergence. Complete matching episodes then run the original native SWE-bench verifier and compare the historical resolved result. Verifier agreement is a replay fidelity check and says nothing about a new model's ability. First-divergence episodes remain failed replay attempts rather than being silently repaired or discarded.

## Native initialization and execution

`code/profile_replay.py` builds each fresh owned E2B sandbox from its mapped template, then creates `Toolbox.from_specs` and enters the native persistent tools. Native ReAct performs `cd -- /testbed` on its shell before the first logged model action; the replay repeats this as a separately labeled preparation step. It does not count that setup call among the 143 model-declared actions.

For this **SWE-bench** corpus the initial Git tree is the template's tree, exactly as in native `SWEBenchTask.run`. The runner does not reset it to the dataset's base commit or apply SWE-reBench's history sanitizer. SWE images may include an environment-setup commit after the dataset base commit. The separate SWE-reBench replay mode still uses its native sanitizer.

This describes the historical SWE-bench initializer. The new 100-step training configuration adds its own Git-history sanitization; replay deliberately calls Sandbox/Toolbox directly and does not inherit that new Task initializer. Each replay result captures initial Git HEAD and tree with a separately labeled read-only probe, whether HEAD equals the declared dataset base commit, and the original/current image and mapped template/build identities. Historical HEAD/tree hashes were not captured, so the probe records the new environment without claiming exact historical tree equality. First observation divergence still stops the episode.

Each episode's model-declared actions execute serially with the same persistent toolbox. Different episodes use a bounded semaphore; the initial concurrency is two. No model is called. Normal exceptions and cancellation exit the sandbox context; an independent run-owned cleanup controller remains necessary after process aborts. Replays should run after template qualification and outside the measured RL baseline to avoid mixing their load into training attribution.

Audit only, without SDK/runtime/credential imports:

```bash
python3 -B /data2/yuhanya/e2b-rl-pathfinding-20260920/code/profile_replay.py \
  --manifest /data2/yuhanya/e2b-rl-pathfinding-20260920/data/replay-corpus/older8b-v2/manifest.jsonl \
  --output /data2/yuhanya/e2b-rl-pathfinding-20260920/results/replay-review
```

Execute in the prepared host CPU environment, after other qualification load has drained:

```bash
UA_EXPERIMENT_ROOT=/data2/yuhanya/e2b-rl-pathfinding-20260920 \
UA_RUN_ID=ua-e2b-pathfinding-20260920 \
UA_PROFILE=1 \
PYTHONPATH=/data2/yuhanya/e2b-rl-pathfinding-20260920/code:/data2/yuhanya/e2b-rl-pathfinding-20260920/src/uni-agent \
/data2/yuhanya/e2b-local-20260916/client-env/bin/python -B \
  /data2/yuhanya/e2b-rl-pathfinding-20260920/code/profile_replay.py \
  --manifest /data2/yuhanya/e2b-rl-pathfinding-20260920/data/replay-corpus/older8b-v2/manifest.jsonl \
  --output /data2/yuhanya/e2b-rl-pathfinding-20260920/results/replay-older8b-asap \
  --concurrency 2 --schedule asap --execute
```

The provider loads the existing private endpoint configuration at runtime; this command does not print credentials. The output directory must be fresh. The equivalent container paths use `/experiment`; source references in the manifest remain provenance and the full actions are embedded, so the original historical `/data2` trees need not be mounted for execution.

Reproduce extraction with the CPU client environment, which supplies pyarrow:

```bash
/data2/yuhanya/e2b-local-20260916/client-env/bin/python -B \
  /data2/yuhanya/e2b-rl-pathfinding-20260920/code/profile_extract_replay.py \
  --source /data2/yuhanya/swe-e2b-rl-20260914/run/swe8b300_20260914_a/agent_logs/attempt-001 \
  --source /data2/yuhanya/uni-agent-lab/runs/rl/swe_separate_2/agent_logs \
  --template-map /data2/yuhanya/e2b-rl-pathfinding-20260920/configs/datasets-template-map.json \
  --metadata-parquet /data2/yuhanya/e2b-rl-pathfinding-20260920/data/qualified/val.parquet \
  --output /data2/yuhanya/e2b-rl-pathfinding-20260920/data/replay-corpus/reproduced \
  --episodes 8
```

Validation at preparation time: seven CPU-only tests pass, including action-count mismatch detection, payload omission, provider cancellation/permit release, and partial training-step accounting. Native replay/profile imports also pass in the specified CPU client environment. These checks validate the executable path and extraction; service execution and replay fidelity remain to be measured.
