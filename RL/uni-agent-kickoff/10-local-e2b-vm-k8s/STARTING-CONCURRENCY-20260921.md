# Sandbox creation concurrency: 1024

**Historical setting:** on September 22 the user requested creation admission 512
and VM RAM approximately 2.2 TiB. See [the newer configuration](VM-MEMORY-20260922.md).
The results below remain the September 21 deployment record.

The user requested creation concurrency 1024 on September 21, 2026. The existing
E2B orchestrator was rebuilt and deployed with
`E2B_MAX_STARTING_INSTANCES_PER_NODE=1024`. Deployment completed at **15:34:51 UTC**.
The running binary hash and its process environment were checked after rollout.

| Layer | Setting after this change | Meaning |
|---|---:|---|
| Live orchestrator startup admission | 1024 | Concurrent start/resume permits per node |
| Node and team resident quota | 512 | Admission limit for simultaneously resident instances |
| New RL client shared creation gate | 1024 | Per-run Ray admission actor, when launched through the new entry point |
| New RL client local startup gate | 1024 | Per-process/event-loop Uni-Agent startup semaphore |
| New RL client exec gate | 16 | Concurrent gated foreground provider calls per run |

The stopped R2 client has not been relaunched. Its archived source and results
still describe create=3 / exec=16. The new client profile is consumed by a separate
entry point so the old supervisor cannot silently omit the admission variables.
The existing 256-session RL recipe and the service's 512 resident quota also limit
how many simultaneous starts a workload can actually generate or admit.

## What execution concurrency 16 actually means

`runtime_launch.py` creates one named `e2b-admission` Ray actor in the run's
namespace. Training and validation providers share it. `e2b_provider.py::_exec`
holds one of its 16 exec permits across a foreground SDK `commands.run` call.

This is not a service-wide limit on running programs. The stateful shell submits
commands through detached tmux, so the program can continue after the submitting
SDK call releases its permit. Subsequent polling calls acquire permits separately.
Readiness/tmux setup calls through `_sdk_command` and native file operations bypass
this exec gate. Independent SDK users do not use this Ray actor.

Source references in the preserved R2 workspace:

- `code/runtime_launch.py:139`: actor creation and default 3/16 settings.
- `code/e2b_provider.py:53`: shared actor lookup; `:92`: ungated setup commands;
  `:278`: gated foreground execution.
- `src/uni-agent/uni_agent/tools/shell.py:246`: detached tmux submission and polling.
- `src/uni-agent/uni_agent/sandbox/base.py:193`: separate local startup semaphore.

## Deployment and verification

Only the orchestrator binary was rebuilt, using Go 1.26.8 and the existing pinned
runtime source `e7b22501988c826563bf1b3cf0d22a1183e7affe` with its existing local
ordinary-page and resident-quota patches. The new increment adds a positive-integer
environment fallback for `max-starting-instances-per-node`; unset still means 3,
and an explicitly configured LaunchDarkly value retains priority.

New orchestrator version: `local-4k-512-start1024-20260921`.

```text
orchestrator SHA-256:
590d0173ff9df2654c67978ac82fc503264ff97890e5b5cdf0e102a0953a238a
```

Before deployment, the node had no live Firecracker processes or sandbox cgroups.
The previous binary and configuration were backed up. The existing core StatefulSet
was stopped, the binary replaced while stopped, the environment/annotation patch
applied, and the core restored to one ready replica. The outer VM was not restarted.

Validation:

- Feature-flag package race tests passed with the environment unset and with 1024.
  These cover parsing, invalid inputs, actual flag initialization, offline fallback,
  missing remote values, and remote-value precedence.
- Orchestrator tests verify 1024 simultaneous semaphore permits and rejection of
  the 1025th, using the real adjustable semaphore without launching microVMs.
- Live process environment is 1024 and the running executable matches the new hash.
  LaunchDarkly is not configured in this local deployment.
- Eight concurrent SDK creates succeeded, followed by command and file checks;
  all eight instances were deleted and the ownership query returned zero residuals.
- New client environment forwarding was exercised in an isolated CPU-only container:
  Docker, Hydra string parsing, and a real Ray worker received 1024 / 1024 / 16.

This is a configuration change with a small functional smoke check. It is **not**
a 1024-way sustained-load or capacity certification. The 512 resident quota remains
an independent constraint, and no RL training or model evaluation was started.

## Reproduce the build and client configuration

Apply `patches/runtime-4k-512.patch` to the pinned upstream source, then apply the
incremental [startup patch](patches/runtime-starting-concurrency.patch). Build the
orchestrator into a new output directory:

```bash
E2B_RUNTIME_VERSION=local-4k-512-start1024-20260921 \
E2B_RUNTIME_OUTPUT_DIR=/path/to/new-output \
bash code/build_runtime_4k.sh orchestrator
```

The Kubernetes overlay `configs/kubernetes/runtime-local.yaml` supplies 1024 to
the orchestrator. A changed executable hash must be recorded in the overlay before
deployment. The existing API and migrator remain their previously verified binaries.

For a separately prepared new RL experiment/container, use
[client-admission.json](configs/client-admission.json) and
[launch_rl_with_admission.py](code/launch_rl_with_admission.py):

```bash
python3 code/launch_rl_with_admission.py \
  --container NEW_CONTAINER --root /path/to/new-experiment --run-id NEW_RUN_ID
```

The default checks Docker mount identity and prints the command. Explicit
`--execute` starts that new client's normal training entry point. It passes all
three admission values to both the driver (`docker exec -e`) and Ray workers
(Hydra string overrides). It refuses the frozen R2 root/identity and output paths
that resolve outside the new experiment. Do not invoke the old R2 supervisor:
its hard-coded environment omits these settings. Creating a new training run or
resuming checkpoint 6 requires the separate run configuration work already recorded
in the stopped-run review.

## Evidence and rollback

Public results are under `results/starting-1024/`. Full local change materials are
in `/data2/yuhanya/e2b-local-20260916/migrations/20260921-starting-1024/`.
Private VM backups are in
`/opt/e2b-local/migrations/20260921-starting-1024/before/`.

Rollback requires an idle node: stop the core StatefulSet, restore the backed-up
orchestrator and runtime overlay, restore its original version/hash annotations,
remove `E2B_MAX_STARTING_INSTANCES_PER_NODE` from the orchestrator environment, then
restore the replica and check readiness. The deployment script includes this
rollback path for a failed rollout; the successful deployment did not exercise it.
