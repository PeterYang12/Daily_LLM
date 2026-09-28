# Recorded decisions and discussion — September 21, 2026

**Later authorized change (15:34 UTC):** the user subsequently requested creation concurrency 1024. The orchestrator has now been rebuilt and deployed with that setting; see [deployment and client admission details](../../RL/uni-agent-kickoff/10-local-e2b-vm-k8s/STARTING-CONCURRENCY-20260921.md). The discussion below is historical. Its stopped-R2 settings and checkpoint decisions remain as recorded; it does not describe the new service startup limit.

**Status: documentation only.** The user explicitly requested that the ideas below be recorded and discussed before any operational changes. R2 remains stopped after six completed native steps with checkpoint 6 preserved. No configuration change, checkpoint deletion, service restart, new benchmark or training launch is authorized by this discussion.

This note supersedes the historical per-step/two-checkpoint policy as the intended policy for future implementation, while preserving the existing run's configuration and evidence. See [the stopped-run review](REVIEW-20260921.md) for completed work and measurements.

## Checkpoint policy requested by the user

| Item | Recorded intent | Implementation status |
|---|---|---|
| Regular save frequency | Save every **20 native training steps** | Not applied |
| Retention | Keep **only the latest complete checkpoint**, with no long-term checkpoint history | Not applied; current checkpoints are untouched |
| Disk use | Avoid accumulating repeated full model/optimizer dumps | Recorded requirement |
| Synchronous versus asynchronous saving | The user objects to the current expensive per-step synchronous saves. The concrete requested change is 20-step cadence and latest-only retention; an asynchronous save mechanism has not been selected | Separate design decision; do not equate lower save frequency with asynchronous writes |

Suggested implementation detail for later review: commit and verify a new checkpoint before removing the previous complete checkpoint. Steady-state retention would be one; save-time peak space can temporarily include old and new copies. The existing checkpoint callback currently requires synchronous completion, so merely toggling `async_save` is not an implemented or verified solution. Exceptions for final/interrupted short runs and the treatment of existing historical checkpoints should be addressed when implementation is actually requested.

## Creation and command limits belong to different layers

The shorthand `create=3, exec=16` describes the **RL client's Ray admission actor**. It does not name two E2B service-wide resource limits.

| Layer | Existing setting and source | What is configurable |
|---|---|---|
| RL client creation admission | `UA_CREATE_PERMITS`, default 3 in `code/runtime_launch.py` | Configurable when preparing a new client run. In R2, the permit also covers readiness and tool setup |
| RL client command admission | `UA_EXEC_PERMITS`, default 16 in `code/runtime_launch.py` | Configurable when preparing a new client run. No evidence establishes an E2B-wide hard command cap of 16 |
| E2B orchestrator starts/resumes per node | Feature flag `max-starting-instances-per-node`, default 3; the semaphore refreshes from the flag every 30 seconds | Configurable through LaunchDarkly. Existing local deployment records show no configured LaunchDarkly, and no local start-limit environment override exists in this version; a local environment-variable approach would require adding that entry point and rebuilding/deploying later |
| E2B resident sandbox quota | Local `E2B_MAX_SANDBOXES_PER_NODE` and team quota are configured for 512 in the archived deployment | Distinct from start concurrency and command concurrency. Increasing resident quota does not raise either admission limit |

Changing only the client's creation limit does not remove the service-side start limit. Conversely, matching both numeric values to 3 does not make their permit scopes identical. Candidate changes must preserve separate counters and measure first usable command, service readiness, client queueing and cleanup, rather than relying only on create acknowledgements.

Read-only source references: `R2/code/runtime_launch.py:139`, `R2/code/admission.py:141`; infra checkout `build/runtime-4k-20260920/packages/shared/pkg/featureflags/flags.go` and `packages/orchestrator/pkg/server/sandboxes.go` under `/data2/yuhanya/e2b-local-20260916`.

## envd timeouts may involve configuration, but are not yet diagnosed

The archived service version supports two distinct readiness budgets:

- **Overall envd readiness/init wait:** feature flag `envd-timeout-milliseconds`; its fallback reads **`ENVD_TIMEOUT`**, a Go duration string such as `10s`. The fallback is 10 seconds. The flag provider can override the fallback.
- **One internal envd init request:** feature flag `envd-init-request-timeout-milliseconds`, default **50 ms**. It is retried within the overall wait. No equivalent local environment override was identified for this separate deadline.

These are separate from the RL client's 1,800-second startup budget, its tool-initialization budget, per-command timeout, sandbox lifetime and total episode deadline. They must not be treated as one timeout setting.

The observed readiness errors are compatible with a budget that is too short for some starts, but do not establish that explanation. Increasing the overall wait may turn a late successful start into success; it does not itself make startup faster or explain why the instance was late. A failure caused by a stuck guest, scheduling delay, snapshot/page loading, network or init behavior needs different treatment. Record the phase that exceeded its budget and whether a longer budget actually reaches readiness.

Provenance checked locally: the archived orchestrator binary hash agrees with the deployment records, and the archived binary contains the relevant timeout/feature-flag names. This discussion did not query or modify the current service process environment, so exact live override values were not re-established.

## Task heterogeneity and rollout budgets

The user suspects that different tasks explain substantial rollout variation and proposes limiting sequence length and/or applying strict timeouts. This is a valid confounder to investigate. The earlier observation that steps 1 and 2 have similar aggregate context tokens but different collection times does **not** isolate an infrastructure cause: task identity, test commands, turn count, length distribution, longest trajectories and model-request arrival patterns all differ.

Record the following principles for a later comparison:

1. Fix task identities, initial model state, templates and prompt/seed settings as far as possible. A fixed seed alone does not guarantee identical generated trajectories; exact replay requires a fidelity check.
2. Define sequence-length limits precisely: model input/context length, generated tokens per call and total generated tokens per episode are different quantities. Existing `response_length` includes interstitial/tool context and cannot be treated as model output length.
3. Consider a bounded episode wall-clock deadline together with token and turn budgets. Per-command, environment-init, verifier and client-queue deadlines should remain separately visible.
4. Define what happens at each limit: stop generation, cancel the actual remote operation where supported, clean up the owned sandbox and preserve an explicit termination reason. A local timeout must not leave remote work silently continuing.
5. Keep model/task failure, budget truncation, service failure and user cancellation separate. Report task success and usable-trajectory yield alongside latency so that dropping hard tasks is not mistaken for an infrastructure speedup.

**No new sequence-length, turn-count or timeout value was selected in this discussion.** These are pending design choices, not configuration changes.

## Priority: sandbox infrastructure before another long RL run

The user proposes making sandbox infrastructure reliable before committing more time to long RL training. The recommended direction is to keep the 100-step run stopped and prioritize the service plus its SDK/client integration.

The completed short RL prefix still has value: it verified actual updates and recovery, exercised realistic multi-turn tasks, and exposed admission, readiness, transport and batch-tail behavior. Continuing toward 100 steps now provides limited new infrastructure diagnosis while mixing task difficulty, model behavior and service effects; the current 62-task training subset and lack of post-training evaluation also limit model-quality conclusions.

Suggested readiness criteria for future planning, with numerical thresholds still to be agreed:

- Reliable create → envd ready → first real command → reset/delete behavior, including failed-create and timeout cleanup.
- Separate cold/warm/template-dependent startup distributions and a bounded-load capacity curve, with first-command failures shown explicitly.
- No leaked processes, sandbox instances, admission permits or unbounded memory/disk accumulation after success, failure or cancellation.
- Request-linked timing and error classification across client, proxy/API, orchestrator and envd; queue wait is distinguished from execution time.
- Frozen representative real command/test workloads with checked replay fidelity, so the service can be evaluated without model generation or parameter updates.
- A short end-to-end RL integration check only after the infrastructure/client results are satisfactory; long RL training follows that review.

This note is a record of the requested direction and the assistant's technical assessment. It is not approval to execute those diagnostics, apply the proposed settings, delete historical checkpoints or resume training.
