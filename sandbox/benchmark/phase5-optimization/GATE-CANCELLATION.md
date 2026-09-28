# Cancellation-safe sandbox admission

The first real RL attempt stalled before its first optimizer update. At the live stall inspection, **85 training task results had completed and native training updates were still 0**. The final interrupted artifacts contain 86 completed training results, while many other episodes have no terminal result. Those completed task results are not completed RL training steps. The original run was stopped and its owned sandboxes were cleaned; its runtime files and evidence were preserved.

The client admission path contained a reproducible cancellation bug. Awaiting a Ray actor's `acquire_exec.remote()` locally does not ensure that the remote acquire stops when that local await is cancelled. A queued remote acquire could later consume a permit after its caller had disappeared. The old client performed that await outside its release `finally` block, so it had no way to reconcile the late grant.

This explains why apparently balanced successful-call counters were insufficient. The final R1 trace contains **20,498 acknowledged exec-gate acquisitions and 20,498 releases**, plus **57 cancelled gate-wait spans**. A grant delivered only inside the actor after local cancellation need not appear among those acknowledged client acquisitions. Final interrupted spans are incomplete observations, not a count of operations still running after shutdown.

## Controlled reproduction

An isolated real-Ray CPU cluster reproduced the defect with one permit:

1. Caller A acquires the permit.
2. Caller B submits a remote acquire and waits; its local task is cancelled.
3. A releases. The still-running remote acquire grants the permit to B.
4. A fresh caller C cannot acquire and times out.

The legacy test observed two grants, one release and zero available permits. It used no E2B service calls or GPU imports. This is an admission-accounting defect; no hardware explanation is needed for this reproduced behavior.

## Repair

R2 gives every create and exec acquisition a unique ticket. The actor tracks queued, granted, cancelled and released states. Cancellation can arrive before the acquire, while queued, or after grant; cancellation and release are idempotent and never add an extra permit. Unknown releases do not change capacity.

The provider shields the original acquire task so it can still observe the RPC's terminal result. On a local exception or cancellation it sends a ticket cancellation, shields and drains that acknowledgement, and waits for the original acquire RPC to settle. Normal completion similarly waits for release acknowledgement. Only then may it retire the ticket record. If settlement or retirement is not acknowledged, it retains and reports the protective record. Repeated caller cancellation is deferred while that cleanup finishes, then re-raised.

The create/exec capacities remain **3 and 16**. The fix does not manually refill the pool, reclaim leases by age, raise concurrency, or retry a command to repair accounting. Command arguments, nonzero-exit results and exceptions retain their original behavior. R2's separate 1,800-second startup budget is an additional declared setting; it is not what makes the cancellation regression pass.

Each real acquisition now produces metadata in `results/gate-ticket-events.jsonl`: requested ticket, grant or local cancellation, actor acknowledgement, original-RPC settlement and retirement/retention. Actor heartbeat snapshots independently expose pool invariants, queue lengths, progress counters and active-lease ages.

## Validation

The final actor/provider pair passed **11 real-Ray regression cases**, including:

- The legacy leak reproduction and fixed queued cancellation for both create and exec.
- A grant arriving during cancellation, repeated cancellation while release acknowledgement is delayed, and cancellation before acquire delivery.
- Duplicate/unknown release, nonzero command exit, SDK failure and cancellation during a command.
- An acquire-RPC failure that retains its protective tombstone.
- Separate **256-waiter storms** for create and exec, using the real capacities of 3 and 16.
- Observer ownership, fresh heartbeat data, active-lease age, and stopping further observation writes.

Both 256-waiter storms ended with **zero active tickets, zero queued tickets, zero retained normal ticket records, and all 3/16 permits available**. Fresh acquisitions succeeded afterwards. The tests used a distinct local Ray namespace and temporary directory, with no E2B calls and `torch_imported=false`. Cancellation counts vary with scheduling; exact observed counts and invariant snapshots are preserved in the [regression summary](results/gate-cancellation-summary.json) and [case CSV](results/gate-cancellation-tests.csv).

The fixed provider was then exercised against **eight actual R2 sandboxes: four PyJWT and four Flask**, through SDK 2.50.0 and the shared ticket actor. All eight passed startup, command execution, file byte round-trip and persistent-shell cwd/environment checks. The smoke run requested and retired **128 tickets: eight create and 120 exec**. Both pools returned to full capacity, and the final listing found **zero R2-owned sandboxes**. It performed no model, GPU or reward-control work. [Smoke CSV](results/gate-provider-smoke.csv).

Tested source identities:

| Component | SHA-256 |
|---|---|
| Final admission actor | `7fc1a43d9b6462b84b14aee3848e2cefc4096c6ef8801986ee1d7bf80a55d0b6` |
| Provider | `2d68ad04cabb8a8c18141a12d8bd1b4c3859034057d92b36771f3cb4b1791ac8` |

The provider was applied only to `/data2/yuhanya/e2b-rl-pathfinding-20260920-r2/code/e2b_provider.py`. The original frozen provider remains unchanged. Private reproducer logs, source hashes, R1 evidence accounting and live smoke proof are under R2 `results/admission-cancellation-validation/` and `results/provider-smoke.json`. The temporary old-container CPU test environment was stopped after testing.

These results validate the repaired admission protocol and its native-provider integration. They do not establish that the subsequent 100-step training run has completed or quantify an RL throughput gain. The new run must still produce its own optimizer, checkpoint/reload, evaluation and profiling evidence.
