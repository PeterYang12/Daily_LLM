# E2B snapshot and creation incident — 2026-09-20

Sandbox creation recovered at approximately 20:20 UTC. Two independent cached templates passed create, command and delete checks. Direct OCI preparation subsequently passed original-fail/reference-pass grading, file roundtrips and persistent-shell checks.

The run now has all 1,650 task environments available: 1,059 cached templates (559 train, 500 evaluation) and 591 training environments prepared at sandbox startup. All 1,150 training tasks and 500 evaluation tasks are preserved. Direct preparation verifies the original OCI manifest and every layer digest. It adds startup work for uncached training tasks; preparation concurrency is limited to 16.

## Failure evidence

- Snapshot persistence repeatedly returned HTTP500 while copying or syncing managed layers under `/mnt/aenv-snapshots/sandboxes/repository/managed-layers`.
- Reducing to one build and one snapshot did not resolve the failure.
- The materializer foundation, the original native base, and a previously qualified Astropy template subsequently failed to create new VMs. The Astropy probe returned HTTP504 with `{"message":"node timed out"}` after90.5seconds.
- Three helper VMs could not be deleted because the backend retained state `Snapshotting`. The API returned500 for deletion. Their IDs are recorded in the run handoff and cleanup output.

The API did not expose the underlying filesystem errno. Storage capacity, I/O or backend state problems remain possible; disk exhaustion has not been confirmed. Backend logs and disk metrics are needed for root-cause attribution.

## Operational response

Bulk snapshot construction was stopped and successfully completed templates retained. Existing model evaluation continued. New evaluation work was gated on backend health. A watchdog checked two different templates and cleared the gate only after both complete lifecycles succeeded.

The watchdog then ran direct-preparation qualification. It passed without writing new persistent snapshots. Cached templates remain the preferred path; uncached tasks use direct preparation. Bulk snapshot writing remains paused because write-path recovery has not been established.

Training still requires a complete500-task original-model baseline. Existing successful and unsuccessful repair results are retained; only tasks without an application result are retried. Verifier budget exhaustion keeps the native unresolved score and is counted separately.

See the live `STATUS.md` and W&B dashboard for current training progress. This incident record describes the observed failure and recovery, rather than a continuously updated status.
