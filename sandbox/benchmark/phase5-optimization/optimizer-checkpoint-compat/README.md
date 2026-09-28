# Checkpoint observer compatibility correction

The reviewed scalar observer was applied to all eight owned R2 training workers at **2026-09-20 19:48:55 UTC**, before their first optimizer update. Only the `runtime_observe._optimizer_checkpoint_scalars` module binding changed. The application did not call model, optimizer or device methods and did not edit frozen runtime files.

The original helper called `getattr(optimizer, 'optimizer', None)` on Megatron 0.18's `ChainedOptimizer`. That compatibility property asserts when the chain contains more than one optimizer. Read-only inspection confirmed that all eight live workers have two children, corresponding to the dense and expert parameter paths. The replacement follows stored child/inner attributes without invoking that property.

The replacement also reads TE/Apex iteration counters from `param_groups['step']` only for nonempty parameter groups. Megatron checkpoint serialization may normalize empty-group counters, so including them could produce a false save/load difference. Native torch per-parameter `state['step']` remains supported. The output schema is unchanged and remains bounded: `step_counters`, `param_groups`, and explicit `unavailable` diagnostics.

Eight CPU unit tests passed, including the actual installed asserting property, empty-group normalization, torch counter fallback, bounded inspection, and verification that only one module binding changes and later global lookup sees it.

## First guard refusal and corrected validation

The first live application attempt was refused on all eight workers before any binding replacement. Its guard compared raw `marshal.dumps(code)` hashes between an imported function and an isolated AST recompilation. Under the actual Python 3.12.3 interpreter, those hashes differed even though the code objects compared equal and every inspected `co_*` field was equal. Marshal's interning/reference representation is not a canonical semantic code identity.

The revised guard requires actual code-object equality to either the compiled frozen function or the reviewed replacement. Raw marshal hashes remain provenance fields. Frozen file SHA, exact actor identity, node, namespace, run/attempt, PID/start time, rank/world size, `_is_actor=True`, `/experiment` root, and idle-before-update checks remain enforced.

A separate CPU process imported the real runtime module and passed the corrected application regression with no Torch import, no live actor contact and no file edit. Root then performed a fresh live target inspection and the reviewed application. All eight acknowledgements reported code equality before/after and no other changed module bindings.

## Persistence and native reload verification

After the clean checkpoint-1 hold, the identical helper was persisted to disk. The reviewed installer changed only `runtime_observe.py` and `configure.py`, archived the owned stop request, and verified that no old attempt-owned container processes remained. The helper source SHA-256 is:

```text
881992549cf840989b5c36704a244382000d657b606c3a8ce4e3ee983fdd93ef
```

The initial configuration inherited `actor_rollout_ref.actor.optim.use_checkpoint_opt_param_scheduler=false`. The resumed configuration sets it to true, enabling native scheduler restoration. Hydra runtime outputs now go to `/experiment/results/hydra/<attempt-id>` with `chdir=false`; the initial runtime-generated Hydra files were preserved outside the frozen source tree. Model, data, loss, batch size and admission limits are unchanged.

At **20:28:50 UTC**, native reload proofs matched on all eight ranks: saved model samples, optimizer scalar maps and scheduler state. Every exposed optimizer counter and the scheduler `num_steps` were restored to 1. Checkpoint 1 was **427,535,690,122 bytes** (~398.17 GiB). The run resumed with evaluation-0 preserved and began the second training batch.

The model probe remains a bounded sample, not a full tensor-state comparison. Native BF16 model save/load places parameters on GPU before both probes; mbridge copies into the existing parameter layout/dtype, so no additional source-level rounding or registration mismatch was found. Missing checkpoint keys or differing saved/restored samples must remain real failures rather than being waived. Optional optimizer fields that are not exposed must remain marked unavailable.

Artifacts:

- [Approved scalar helper](scalar_observer_v2.py).
- [Guarded inspection/application driver](worker_scalar_patch.py).
- [CPU tests](test_scalar_observer_v2.py).
- [Installed property provenance](installed-property.json).
- [Compatibility summary](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/checkpoint-scalar-compat/compatibility-summary.json).
- [Refused first application](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/checkpoint-scalar-compat/apply-1789933232446200093.json).
- [Real-import CPU regression](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/checkpoint-scalar-compat/cpu-real-import-regression.json).
- [Successful application](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/checkpoint-scalar-compat/apply-1789933735009718078.json).

Checkpoint 1 and its bounded native reload checks are accepted. The 100-step target is still running; use the live phase-4 status for current completion.

- [Accepted checkpoint-1 prefix](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/checkpoint1-prefix-20260920T184935Z-3c9aeb9d.json).
- [Guarded persistence script](persist_after_checkpoint1.py) and [applied source hashes](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/checkpoint1-persistence/applied.json).
- [Operator reload acceptance](/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/results/operator/checkpoint1-resume-accepted.json).
